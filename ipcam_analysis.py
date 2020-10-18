#!/usr/bin/env python3

# ipcam_analysis
# Copyright (C) 2020 aquaticus
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer
from pyftpdlib.authorizers import DummyAuthorizer
import process
import logging
import sys
import yaml
import logging.config
import argparse
import config
from datetime import datetime

log = None
start_time = None
end_time = None


def check_time_window():
    """Returns True if current time is in time window. Otherwise False."""
    if start_time is None or end_time is None:
        return True
    now = datetime.now().time()  # use local time
    if start_time < end_time:
        return start_time <= now <= end_time
    else:
        return now >= start_time or now <= end_time


class ImageProcessingHandler(FTPHandler):

    def on_file_received(self, file):
        if not check_time_window():
            log.info("Image ignored. Not in time window [%s, %s]." % (start_time.strftime('%H:%M'),
                                                                      end_time.strftime('%H:%M')))
        else:
            process.process_image(file, self.username)
        if config.global_config['ftp_server']['remove_files']:
            os.remove(file)
            log.info(f"Removed {file}.")

    def on_incomplete_file_received(self, file):
        # remove partially uploaded files
        log.info(f"Removed partially transferred {file}")
        os.remove(file)


def setup_logging(global_config_file, enable_console_log, log_level):
    with open(global_config_file, 'rt') as f:
        log_config = yaml.safe_load(f.read())
        if enable_console_log:
            log_config['root']['handlers'].append('console')
        log_config['loggers']['ipcam_analysis']['level'] = log_level
        logging.config.dictConfig(log_config)


def start_ftp():
    global start_time
    global end_time

    # parse time window
    time_window = config.global_config.get('time_window')
    if time_window is not None:
        start_time = datetime.strptime(time_window['start'], "%H:%M").time()
        end_time = datetime.strptime(time_window['end'], "%H:%M").time()
        log.info("Time window: [%s, %s]" % (start_time.strftime('%H:%M'), end_time.strftime('%H:%M')))

    permissions = "ewm"  # e=CWD, w=STOR, m=MKD
    authorizer = DummyAuthorizer()
    # Create ftp root dir
    root_dir = config.global_config['ftp_server']['root_dir']
    if not os.path.isdir(root_dir):
        os.mkdir(root_dir)
        log.info(f"Created FTP root directory: {root_dir}")

    for usr, pwd in config.global_config['ftp_server']['users'].items():
        home_dir = os.path.join(root_dir, usr)
        # create home dir
        if not os.path.isdir(home_dir):
            os.mkdir(home_dir)
            log.info(f"Created FTP home directory: {home_dir}")
        authorizer.add_user(usr, pwd, homedir=home_dir, perm=permissions)
        log.info(f'Added FTP user: {usr}')
    handler = ImageProcessingHandler
    handler.authorizer = authorizer
    handler.banner = 'ipcam_analysis ready'
    handler.timeout = 60  # kick out a user when inactive for 1 minute
    server = FTPServer(('', config.global_config['ftp_server']['port']), handler)
    log.info("Started FTP server")
    server.serve_forever()


def test_aws():
    import boto3
    from PIL import Image
    import io
    from botocore.exceptions import ClientError

    print("Testing Amazon AWS")
    print('AWS environment variables')
    dump_aws_env_vars(True, '  ')

    print("Checking AWS Rekognition permissions")
    region = config.global_config["aws_region"]
    print(f"  AWS Region: {region}")
    try:
        rek = boto3.client('rekognition', region_name=region)
        img = Image.new('RGB', (80, 80), color='white')
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='JPEG')
        response = rek.detect_labels(Image={'Bytes': img_byte_arr.getvalue()})
        log.debug(response)
        content_type = response['ResponseMetadata']['HTTPHeaders']['content-type']
        if content_type == 'application/x-amz-json-1.1':
            print("  Response OK")
        else:
            raise Exception("Invalid response from rekognition")

        if len(response['Labels']) > 0:
            print('  Objects detected')
        else:
            print('WARNING: No object detected')
    except ClientError as e:
        log.exception(e)
        print("REKOGNITION ERROR")
        print(e)
        return -2

    print("Testing email SES settings")
    client = boto3.client('ses', region_name=region)
    email_config = config.global_config['email']
    recipients = email_config['recipients']
    sender = email_config['sender_email']
    print(f"  Email sender: %s" % sender.encode('utf-8'))
    print(f"  Email recipients: {recipients}")
    print(f"  AWS Region: {region}")
    try:
        client.send_email(
            Destination={
                'ToAddresses': recipients
            },
            Message={
                'Body': {
                    'Text': {
                        'Data': 'Amazon SES settings are ok',
                    },
                },
                'Subject': {
                    'Data': 'Email test',
                },
            },
            Source=sender,
        )

    except ClientError as e:
        print(e.response['Error']['Message'])
    else:
        print("  Email successfully sent.")

    print("Test OK")

    return 0


def dump_aws_env_vars(stdout_output=False, prefix=''):
    aws_vars = [
        'AWS_ACCESS_KEY_ID',
        'AWS_SECRET_ACCESS_KEY',
        'AWS_SESSION_TOKEN',
        'AWS_PROFILE',
        'AWS_CONFIG_FILE',
        'AWS_SHARED_CREDENTIALS_FILE',
        'AWS_MAX_ATTEMPTS',
        'AWS_RETRY_MODE'
    ]
    for v in aws_vars:
        val = os.environ.get(v)
        if val is None:
            val = '<not set>'
        fmt = f'{prefix}{v}={val}'
        if stdout_output:
            print(fmt)
        else:
            log.debug(fmt)


def main(argv):
    parser = argparse.ArgumentParser(description='IP Camera Analysis Server',
                                     epilog='License: GNU GPLv3, ' +
                                     'source code: https://github.com/aquaticus/ipcamera_analysis')
    parser.add_argument('-c', '--config', default='config.yaml', help='sets configuration file (default: %(default)s)')
    parser.add_argument('-n', '--enable-console-log', action='store_true', help='enables log to console')
    parser.add_argument('-t', '--test-aws', action='store_true', help='tests Amazon AWS credentials')
    parser.add_argument('-l', '--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                        default='INFO', help='sets log level (default: %(default)s)')

    args = parser.parse_args(argv)

    config.load(args.config)

    log_level = logging.getLevelName(args.log_level)
    setup_logging(config.global_config['log_configuration_file'], args.enable_console_log, log_level)

    global log
    log = logging.getLogger('ipcam_analysis')

    if args.test_aws:
        return test_aws()

    print("IP Camera Analysis started")

    log.info("Starting server...")
    log.info("Log level %s" % (logging.getLevelName(log.level)))
    log.debug("Debug output enabled")

    dump_aws_env_vars()

    start_ftp()

    return 0


if __name__ == "__main__":
    exit(main(sys.argv[1:]))
