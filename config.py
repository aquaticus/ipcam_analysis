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

import logging
import os
import yaml

global_config = None


def load(config_file_yaml):
    global global_config
    log = logging.getLogger('ipcam_analysis.config')
    log.info('Loading configuration from %s' % os.path.abspath(config_file_yaml))
    with open(config_file_yaml) as f:
        global_config = yaml.load(f, Loader=yaml.FullLoader)
    log.debug(global_config)


def get_config():
    if global_config is None:
        raise Exception('Load configuration first')
    return global_config
