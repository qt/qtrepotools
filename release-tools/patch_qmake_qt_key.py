#!/usr/bin/env python
###############################################
#
# Copyright (C) 2012 Digia Plc
# For any questions to Digia, please use contact form at http://qt.digia.com
#
# $QT_BEGIN_LICENSE:LGPL$
# GNU Lesser General Public License Usage
# This file may be used under the terms of the GNU Lesser General Public
# License version 2.1 as published by the Free Software Foundation and
# appearing in the file LICENSE.LGPL included in the packaging of this
# file. Please review the following information to ensure the GNU Lesser
# General Public License version 2.1 requirements will be met:
# http://www.gnu.org/licenses/old-licenses/lgpl-2.1.html.
#
# GNU General Public License Usage
# Alternatively, this file may be used under the terms of the GNU General
# Public License version 3.0 as published by the Free Software Foundation
# and appearing in the file LICENSE.GPL included in the packaging of this
# file. Please review the following information to ensure the GNU General
# Public License version 3.0 requirements will be met:
# http://www.gnu.org/copyleft/gpl.html.
#
# $QT_END_LICENSE$
#
# If you have questions regarding the use of this file, please use
# contact form at http://qt.digia.com
#
###############################################

"""Scripts to patch keys inside qmake executable"""

import os
import sys
import shutil
import re
import datetime


##############################################################
# Function
##############################################################
def replace_key(qmake_file_name, key, new_value):
    if not os.path.exists(qmake_file_name):
        print '*** Unable to open file: ' + qmake_file_name
        return False
    if not key:
        print '*** Given key empty! '
        return False
    if not new_value:
        print '*** Given new_value empty! '
        return False
    if len(new_value) > 255:
        print '*** Given new_value can not be longer than 255 or the binary would get corrupted! '
        print '*** Length for the given new value was: ' + str(len(new_value))
        return False

    f = open(qmake_file_name, 'r+')
    text = f.read()
    full_length = len(text)
    m = re.search(key + '=', text)
    end_index = m.end()
    if not end_index:
        print '*** Given key not found: ' + key
        print '*** Abort!'
        return False
    remaining_max_length = full_length - end_index
    read_reamaining_offset = end_index + len(new_value) + 1
    replacement_text = text[0:end_index] + new_value + '\0' + text[read_reamaining_offset:]

    f.seek(0)
    f.write(replacement_text)
    f.truncate()
    f.close()
    return


##############################################################
# Function
##############################################################
def fetch_key(qmake_file_name, key):
    if not os.path.exists(qmake_file_name):
        print '*** Unable to open file: ' + qmake_file_name
        return False
    if not key:
        print '*** Given key empty! '
        return False

    f = open(qmake_file_name, 'rb')
    text = ''
    while True:
        temp = f.read()
        if not temp or len(temp) == 0:
            break
        text = text + temp
        print 'got text'

    f.close()

    m = re.search(key + '=', text)
    if not m:
        print '*** Could not find given key: ' + key
        print '*** From: ' + qmake_file_name
        return ''
    end_index = m.end()

    text = text[end_index:]
    end_index_2 = re.search('\0', text)
    value = text[0:end_index_2.end()]
    return value


##############################################################
# Function
##############################################################
if __name__ == "__main__":
    if len(sys.argv) != 4:
        print '*** Three parameters needed!'
        print '*** Abort!'
        sys.exit(-1)
    key = fetch_key(sys.argv[1], sys.argv[2])
    print key
    replace_key(sys.argv[1], sys.argv[2], sys.argv[3])
    sys.exit(0)

