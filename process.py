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
import io
import logging
from email.mime.image import MIMEImage
from timeit import default_timer as timer
import boto3
from botocore.exceptions import ClientError
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from PIL import Image, ImageDraw, ImageColor, ImageFont, UnidentifiedImageError
import config
from pathlib import Path
import datetime
from string import Template

log = None


def format_parents(parents):
    formatted = ""
    for parent in parents:
        formatted += f"{parent['Name']}, "

    if len(formatted) > 0:
        return formatted[:-2]
    else:
        return ""


def parse_labels_new(labels, ignore_labels):
    detected_labels = dict()
    for label in labels:
        name = label['Name']

        if name not in ignore_labels:
            in_parent = False
            for parent in label['Parents']:
                if parent['Name'] in ignore_labels:
                    ignored_parent = parent['Name']
                    in_parent = True
                    break

            if not in_parent:
                p = format_parents(label['Parents'])
                if len(p) > 0:
                    fullname = f"{name} ({p})"
                else:
                    fullname = name
                detected_labels[fullname] = label['Confidence']
            else:
                log.info(f"Ignored label: {label['Name']} (ignored parent: {ignored_parent})")
        else:
            log.info(f"Ignored label: {label['Name']}")

    return detected_labels


def parse_labels_alarm(labels, alarm_labels):
    detected_labels = dict()
    for label in labels:
        name = label['Name']

        if name in alarm_labels:
            detected_labels[name] = label['Confidence']
        else:
            log.info(f"Ignored label: {label['Name']}")

    return detected_labels


def parse_bounding_boxes(labels, detected):
    bounding_boxes = list()
    for label in labels:
        name = label['Name']
        inst = label['Instances']
        for box in inst:
            bounding_boxes.append([name, box])

    return bounding_boxes


def send_email(subject, body_html, attachment_fp=None, attachment_name=None):
    client = boto3.client('ses', region_name=config.global_config["aws_region"])
    email_config = config.global_config['email']

    msg = MIMEMultipart('mixed')
    msg['Subject'] = Header(subject, "utf-8")
    msg['From'] = formataddr((email_config["sender_name"], email_config["sender_email"]))

    body = MIMEMultipart('alternative')
    html = MIMEText(body_html.encode("utf-8"), 'html', "utf-8")

    body.attach(html)
    msg.attach(body)

    if attachment_fp is not None:
        attachment_fp.seek(0)
        att_data = attachment_fp.read()
        log.debug('Email Attachment size %d' % len(att_data))
        attachment = MIMEImage(att_data)
        attachment.add_header('Content-Id', '<image01>')
        attachment.add_header('Content-Disposition', 'attachment', filename=attachment_name)

        msg.attach(attachment)

    try:
        response = client.send_raw_email(
            Source=msg['From'],
            Destinations=email_config['recipients'],
            RawMessage=
            {
                'Data': msg.as_string(),
            },
        )
    except ClientError as e:
        # catch email problems
        log.error("Sending email failed")
        log.exception(e)
    else:
        log.info(f'Email sent to: {email_config["recipients"]}')


def load_image(image_data):
    try:
        img = Image.open(io.BytesIO(image_data))
    except UnidentifiedImageError:
        raise Exception('Unknown image file format')

    if img.format != 'JPEG' and img.format != "PNG":
        raise Exception(f'Unsupported image format {img.format}')

    return img


def process(image_file, camera_name):
    with open(image_file, "rb") as f:
        image_binary = f.read()

    img_size = len(image_binary)
    log.debug(f'Image size: {img_size} bytes')

    if img_size == 0:
        raise Exception("Image file is empty")

    img = load_image(image_binary)
    conf = config.global_config['rekognition']

    start = timer()

    ext = Path(image_file).suffix
    timestamp_filename = camera_name + '_' + datetime.datetime.now().isoformat() + ext

    rek = boto3.client('rekognition', region_name=config.global_config["aws_region"])

    response = rek.detect_labels(Image={'Bytes': image_binary}, MinConfidence=conf['min_confidence_percent'])

    end = timer()
    log.debug(response)
    log.info("PROCESSING TIME %.1fs", end - start)

    if config.global_config['new_labels_only']:
        log.info('New labels mode')
        detected_labels = parse_labels_new(response["Labels"], config.global_config['labels'])
    else:
        log.info('Alarm labels mode')
        detected_labels = parse_labels_alarm(response["Labels"], config.global_config['labels'])

    if len(detected_labels) == 0:
        log.info("Nothing detected. No any interesting labels.")
        return

    log.debug(f'Configured labels {config.global_config["labels"]}')
    log.info('DETECTED LABELS:')
    for label, confidence in detected_labels.items():
        log.info("    %s %.1f%%" % (label, confidence))

    # Draw bounding boxes for all detected labels
    boxes = parse_bounding_boxes(response["Labels"], detected_labels.keys())
    if len(boxes) > 0:
        img = draw_bounding_box(boxes, img, config.global_config["rekognition"]["image_resize_percent"])
    else:
        log.info("No bounding boxes")

    # image + bounding boxes
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='JPEG')

    list_html = "<ul>"
    for label, confidence in detected_labels.items():
        list_html += "<li><b>{0}</b>: {1:.0f}%</li>".format(label, confidence)
    list_html += "</ul>"
    image_html = f'<img src="cid:image01" width="{img.width}" height="{img.height}"/>'

    message_html = '<html><body>'
    message_html += config.global_config['email']["message_html"]
    message_html += "</body></html>"

    # substitute tokens
    tokens = {'camera': camera_name, 'list': list_html, 'image': image_html}

    subject = Template(config.global_config['email']['subject']).safe_substitute(tokens)
    body = Template(message_html).safe_substitute(tokens)

    send_email(
        subject,
        body,
        img_byte_arr,
        os.path.basename(timestamp_filename)
    )


def draw_bounding_box(box_data, image, resize):
    w = int(image.width * resize / 100)
    h = int(image.height * resize / 100)
    rim = image.resize((w, h))
    color = ImageColor.getrgb('yellow')

    draw = ImageDraw.Draw(rim)

    for item in box_data:
        box = item[1]['BoundingBox']
        x0 = int(w * box['Left'])
        y0 = int(h * box['Top'])
        x1 = int(x0 + w * box['Width'])
        y1 = int(y0 + h * box['Height'])

        draw.rectangle([x0, y0, x1, y1], outline=ImageColor.getrgb("yellow"))

        text = "%s %s%%" % (item[0], int(item[1]['Confidence']))

        font = ImageFont.load_default()

        (tw, th) = font.getsize(text)

        if y1 + th <= h:
            log.debug(f"Drawing bounding box on bottom ({x0},{y0})-({x1},{y1}) {text}")
            draw.text((x0, y1), text, fill=color)
        else:
            log.debug(f"Drawing bounding box on top ({x0},{y0})-({x1},{y1}) {text}")
            draw.text((x0, y0 - th), text, fill=color)

    return rim


error_email_sent = False


def process_image(image_file, camera_name):
    global log
    global error_email_sent

    log = logging.getLogger('ipcam_analysis.process')

    if image_file is None:
        raise Exception("Image file name cannot be empty")
    elif not os.path.exists(image_file):
        raise Exception("Image file {image_file} does not exist")

    log.info(f"Processing image: {image_file} from {camera_name}")

    try:
        process(image_file, camera_name)
    except Exception as e:
        log.exception(e)
        if not error_email_sent:
            body = f'<p>Failed to process image. Check logs for details.</p><p>Server: {os.uname()[1]}</p>'
            send_email('Image processing error', body)
            log.info('Error email sent')
            error_email_sent = True  # do not send emails when error occurs one by one
        else:
            log.info('Error email not sent.')
        return

    # reset error flag if processing was successful
    error_email_sent = False
