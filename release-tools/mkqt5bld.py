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

import os
import re
import shutil
import subprocess
from subprocess import PIPE, STDOUT
import sys
import urllib
import fileinput
import bldinstallercommon
import patch_qmake_qt_key

SCRIPT_ROOT_DIR                     = os.getcwd()
WORK_DIR_NAME                       = 'qt5_workdir'
WORK_DIR                            = SCRIPT_ROOT_DIR + os.sep + WORK_DIR_NAME
QT_SRC_PACKAGE_URL                  = ''
QT_PACKAGE_SAVE_AS_TEMP             = ''
QT_SOURCE_DIR                       = WORK_DIR + os.sep + 'w'
MAKE_INSTALL_ROOT_DIR               = WORK_DIR + os.sep + 'qt5_install_root' #main dir for submodule installations
CONFIGURE_CMD                       = ''
MAKE_CMD                            = ''
MAKE_INSTALL_CMD                    = ''
MODULE_ARCHIVE_DIR_NAME             = 'module_archives'
MODULE_ARCHIVE_DIR                  = SCRIPT_ROOT_DIR + os.sep + MODULE_ARCHIVE_DIR_NAME
MAIN_INSTALL_DIR_NAME               = 'main_install'
MODULE_PRI_FILES_DIR_NAME           = 'modules_pri_files'
ALL_MODULE_PRI_FILES_DIR_NAME       = 'all_modules_pri_files'

QT5_MODULES_LIST                    = ['qt3d', 'qlalr', 'qtactiveqt', 'qtbase', 'qtconnectivity', 'qtdeclarative', 'qtdoc', 'qtdocgallery', 'qtfeedback', 'qtgraphicaleffects', 'qtimageformats', 'qtjsondb', 'qtjsbackend', 'qtlocation', 'qtmultimedia', 'qtphonon', 'qtpim', 'qtqa', 'qtquick1', 'qtrepotools', 'qtscript', 'qtsensors', 'qtsvg', 'qtsystems', 'qttools', 'qttranslations', 'qtwayland', 'webkit', 'qtwebkit-examples-and-demos', 'qtxmlpatterns']

CONFIGURE_OPTIONS                   = '-opensource -release -nomake tests -confirm-license' #-make examples
DEVEL_MODE                          = 0
FORCE_MAKE                          = 0
RUN_RPATH                           = 0
ORIGINAL_QMAKE_QT_PRFXPATH          = ''



###############################
# function
###############################
def init_mkqt5bld():
    global CONFIGURE_CMD
    global CONFIGURE_OPTIONS
    global MAKE_CMD
    global MAKE_INSTALL_CMD

    print '----------------------- Initializing build -------------------------'
    if bldinstallercommon.is_linux_platform():          #linux
        CONFIGURE_CMD = './'
        CONFIGURE_OPTIONS += ' -no-gtkstyle'
    elif bldinstallercommon.is_mac_platform():          #mac
        CONFIGURE_CMD = './'
        #CONFIGURE_OPTIONS += ' -make libs -no-pch' <- not sure if these are needed,
        #Added -developer-build to get the sources built, should be removed later..?
        CONFIGURE_OPTIONS = '-release -developer-build -opensource -confirm-license -nomake tests'

    #Add padding to original rpaths to make sure that original rpath is longer than the new
    if bldinstallercommon.is_linux_platform() or bldinstallercommon.is_solaris_platform():
        CONFIGURE_OPTIONS += ' -R ______________________________PADDING______________________________'

    CONFIGURE_CMD += 'configure'
    if bldinstallercommon.is_win_platform():            #win
        CONFIGURE_CMD += '.bat'

    # make cmd
    if MAKE_CMD == '':  #if not given in commandline param, use nmake or make according to the os
        if bldinstallercommon.is_win_platform():        #win
            MAKE_CMD = 'nmake /l /s'
            MAKE_INSTALL_CMD = 'nmake /l /s install'
        elif bldinstallercommon.is_linux_platform():    #linux
            MAKE_CMD = 'make -s'
            MAKE_INSTALL_CMD = 'make -s install'
        elif bldinstallercommon.is_mac_platform():      #mac
            MAKE_CMD = 'make -s'
            MAKE_INSTALL_CMD = 'make -s install'

    if FORCE_MAKE == 1:
        if bldinstallercommon.is_linux_platform() or bldinstallercommon.is_mac_platform():    #linux&mac
            MAKE_CMD = MAKE_CMD + ' -i'
            MAKE_INSTALL_CMD = MAKE_INSTALL_CMD + ' -i'

    #remove old working dirs
    if os.path.exists(WORK_DIR):
        print '    Removing old work dir ' + WORK_DIR
        bldinstallercommon.remove_tree(WORK_DIR)
    if os.path.exists(MODULE_ARCHIVE_DIR):
        print '    Removing old module archive dir ' + MODULE_ARCHIVE_DIR
        bldinstallercommon.remove_tree(MODULE_ARCHIVE_DIR)

    print '    Using ' + MAKE_CMD + ' for making and ' + MAKE_INSTALL_CMD + ' for installing'
    print '    Qt configure command set to: ' + CONFIGURE_CMD + CONFIGURE_OPTIONS
    print '--------------------------------------------------------------------'


###############################
# function
###############################
def fetch_src_package():
    global QT_PACKAGE_SAVE_AS_TEMP
    QT_PACKAGE_SAVE_AS_TEMP = os.path.normpath(WORK_DIR + os.sep + os.path.basename(QT_SRC_PACKAGE_URL))

    print '--------------------- Fetching Qt src package ----------------------'
    if not os.path.isfile(QT_PACKAGE_SAVE_AS_TEMP):
        if not bldinstallercommon.is_content_url_valid(QT_SRC_PACKAGE_URL):
            print '*** Qt src package url is invalid! Abort!'
            sys.exit(-1)
        print '     Downloading:        ' + QT_SRC_PACKAGE_URL
        print '            into:        ' + QT_PACKAGE_SAVE_AS_TEMP
        # start download
        urllib.urlretrieve(QT_SRC_PACKAGE_URL, QT_PACKAGE_SAVE_AS_TEMP, reporthook=bldinstallercommon.dlProgress)
    else:
        print 'Found old local package, using that: ' + QT_PACKAGE_SAVE_AS_TEMP
    print '--------------------------------------------------------------------'


###############################
# function
###############################
def extract_src_package():
    global QT_SOURCE_DIR
    global CONFIGURE_CMD
    print '------------------ Extracting source package -----------------------'

    if os.path.exists(QT_SOURCE_DIR):
        print 'Source dir ' + QT_SOURCE_DIR + ' already exists, using that (not re-extracting the archive)'
    else:
        print 'Extracting source package: ' + QT_PACKAGE_SAVE_AS_TEMP
        print 'Into:                      ' + QT_SOURCE_DIR
        bldinstallercommon.create_dirs(QT_SOURCE_DIR)
        bldinstallercommon.extract_file(QT_PACKAGE_SAVE_AS_TEMP, QT_SOURCE_DIR)

    l = os.listdir(QT_SOURCE_DIR)
    items = len(l)
    if items == 1:
        print '    Replacing qt-everywhere-xxx-src-5.0.0 with a to get shorter path names'
        shorter_dir_path = QT_SOURCE_DIR + os.sep + 's'
        bldinstallercommon.create_dirs(shorter_dir_path)
        print '  moving ' + QT_SOURCE_DIR + os.sep + l[0] + ' to ' + shorter_dir_path
        bldinstallercommon.move_tree(QT_SOURCE_DIR + os.sep + l[0], shorter_dir_path)
        print '    Source dir ' + QT_SOURCE_DIR
        QT_SOURCE_DIR = QT_SOURCE_DIR + os.sep + 's'
        #QT_SOURCE_DIR = QT_SOURCE_DIR + os.sep + l[0]
        print '    new source dir ' + QT_SOURCE_DIR
        #CONFIGURE_CMD = QT_SOURCE_DIR + os.sep + CONFIGURE_CMD   #is this needed in shadow build?
    else:
        print '*** Unsupported directory structure!!!'
        sys.exit(-1)

    #remove not working dirs
    if os.path.exists(QT_SOURCE_DIR + os.sep + 'qttranslations'):
        print '    Removing qttranslations'
        bldinstallercommon.remove_tree(QT_SOURCE_DIR + os.sep + 'qttranslations')
    if os.path.exists(QT_SOURCE_DIR + os.sep + 'qtwebkit'):
        print '    Removing qtwebkit'
        bldinstallercommon.remove_tree(QT_SOURCE_DIR + os.sep + 'qtwebkit')
    if os.path.exists(QT_SOURCE_DIR + os.sep + 'qtwebkit-examples-and-demos'):
        print '    Removing qtwebkit-examples-and-demos'
        bldinstallercommon.remove_tree(QT_SOURCE_DIR + os.sep + 'qtwebkit-examples-and-demos')

# just for testing, remove!!
#    for module in QT5_MODULES_LIST:
#        if module != 'qtbase' and module != 'qtsvg' and module != 'qtxmlpatterns':
#            if os.path.exists(QT_SOURCE_DIR + os.sep + module):
#                bldinstallercommon.remove_tree(QT_SOURCE_DIR + os.sep + module)

    print '--------------------------------------------------------------------'


###############################
# function
###############################
def copy_modules():
    print '--------- Taking back-up copies of .pri files -----------------------'

    temp_base_dir = WORK_DIR + os.sep + MODULE_PRI_FILES_DIR_NAME
    temp_base_dir_all = WORK_DIR + os.sep + ALL_MODULE_PRI_FILES_DIR_NAME
    bldinstallercommon.create_dirs(temp_base_dir)
    bldinstallercommon.create_dirs(temp_base_dir_all)

    for module_name in QT5_MODULES_LIST:
        destination_dir = temp_base_dir + os.sep + module_name
        modules_source_dir_1 = QT_SOURCE_DIR + os.sep + module_name + os.sep + 'modules' + os.sep
        modules_source_dir_2 = QT_SOURCE_DIR + os.sep + module_name + os.sep + 'src' + os.sep + 'modules' + os.sep
        temp_source = modules_source_dir_1
        if not os.path.exists(temp_source):
            temp_source = modules_source_dir_2
        # loop on all files
        for root, dirs, files in os.walk(temp_source):
            for name in files:
                if name.endswith('.pri'):
                    file_full_path = os.path.join(root, name)
                    if not os.path.isdir(file_full_path) and not os.path.islink(file_full_path):
                        destination_dir     = temp_base_dir + os.sep + module_name
                        destination_dir_all = temp_base_dir_all
                        print ' Copying: ' + module_name
                        print '     Src:' + file_full_path
                        print '     Dst:' + destination_dir
                        bldinstallercommon.create_dirs(destination_dir)
                        shutil.copy(file_full_path, destination_dir)
                        # copy also as one chunk
                        shutil.copy(file_full_path, destination_dir_all)

###############################
# function
###############################
def build_qt():
    global QT5_MODULES_LIST

    # configure
    print '--------------------- Configuring Qt -------------------------------'

    cmd_args = CONFIGURE_CMD + ' ' + CONFIGURE_OPTIONS
    print '    configure line: ' + cmd_args
    bldinstallercommon.do_execute_sub_process(cmd_args.split(' '), QT_SOURCE_DIR, True)

    # build
    print '---------------------- Building Qt ---------------------------------'
    #create list of modules in default make order
    regex = re.compile('^make_default:.*') #search line starting with 'make_default:'
    submodule_list = []

    if os.path.exists(QT_SOURCE_DIR + os.sep + 'Makefile'):
        print '    Generating ordered list of submodules from main Makefile'
        makefile = open(QT_SOURCE_DIR + os.sep + 'Makefile', 'r')
        for line in makefile:
            lines = regex.findall(line)
            for make_def_line in lines:
                #print make_def_line
                make_def_list = make_def_line.split(' ')

        #TODO: check if there is more than one line in Makefile
                #change 'module-qtbase-make_default' to 'qtbase'
                for item in make_def_list:
                    if item.startswith('module-'):
                        submodule_name = item[7:]   #7 <- module-
                        index = submodule_name.index('-make_default')
                        submodule_list.append(submodule_name[:index])

        QT5_MODULES_LIST = submodule_list
    else:
        print '    Error, main Makefile not found, using hard coded modules list for make.'

    if os.path.exists(MAKE_INSTALL_ROOT_DIR):   #remove if old dir exists
        shutil.rmtree(MAKE_INSTALL_ROOT_DIR)
    bldinstallercommon.create_dirs(MAKE_INSTALL_ROOT_DIR)   #create install dirs

    #take backups of .pri files to be restored after build
    copy_modules()

    #main level make
    cmd_args = MAKE_CMD

    if bldinstallercommon.is_linux_platform():
        cmd_args += ' -j12'
    print '    Running make on root level'
    bldinstallercommon.do_execute_sub_process(cmd_args.split(' '), QT_SOURCE_DIR, True)

    print '--------------------------------------------------------------------'


###############################
# function
###############################
def patch_rpaths():
    if RUN_RPATH == 1:
        if (bldinstallercommon.is_linux_platform() or bldinstallercommon.is_solaris_platform()):
            print '----------------- Patching rpaths -----------------------------------'
            bldinstallercommon.handle_component_rpath(QT_SOURCE_DIR, 'lib')
            print '---------------------------------------------------------------------'


###############################
# function
###############################
def install_qt():

    print '----------------- Installing Qt -------------------------------------'
    #main level make install
    cmd_args = ''
    if bldinstallercommon.is_linux_platform():
        cmd_args = 'sudo '
    cmd_args += MAKE_INSTALL_CMD
    print '    Running main level make install'
    bldinstallercommon.do_execute_sub_process(cmd_args.split(' '), QT_SOURCE_DIR, True)

#main level install with install root
    #main level make install
    cmd_args = ''
    install_root_path = MAKE_INSTALL_ROOT_DIR + os.sep + MAIN_INSTALL_DIR_NAME
    if bldinstallercommon.is_linux_platform():
        cmd_args = 'sudo '
    if bldinstallercommon.is_win_platform():
        install_root_path = install_root_path[3:]
        print '    on win, use install root path: ' + install_root_path
    cmd_args += MAKE_INSTALL_CMD + ' INSTALL_ROOT=' + install_root_path
    print '    Running main level make install with install root ' + install_root_path
    bldinstallercommon.do_execute_sub_process(cmd_args.split(' '), QT_SOURCE_DIR, True)
#end - main level install with install root

    #make install for each module with INSTALL_ROOT
    print '    Install modules to INSTALL_ROOT'
    for module_name in QT5_MODULES_LIST:
        if module_name == 'qtwebkit':
            print '    > > > > > > NOT installing qtwebkit < < < < < < < <'
        elif module_name == 'qtwebkit-examples-and-demos':
            print '    > > > > > > NOT installing qtwebkit-examples-and-demos < < < < < < < <'
        elif module_name == 'qttranslations':
            print '    > > > > > > NOT installing qttranslations < < < < < < < <'
        else:
            install_root_path = MAKE_INSTALL_ROOT_DIR + os.sep + 'submodule_install_' + module_name
            if bldinstallercommon.is_win_platform():
                install_root_path = install_root_path[3:]
                print '    using install root path: ' + install_root_path
            submodule_dir_name = QT_SOURCE_DIR + os.sep + module_name
            cmd_args = ''

            if bldinstallercommon.is_linux_platform():
                cmd_args = 'sudo '

            cmd_args += MAKE_INSTALL_CMD + ' ' + 'INSTALL_ROOT=' + install_root_path
            print '    Installing module: ' + module_name
            print '    -> cmd args: ' + cmd_args
            print '    -> in: ' + submodule_dir_name

            bldinstallercommon.do_execute_sub_process(cmd_args.split(' '), submodule_dir_name, True)

    print '--------------------------------------------------------------------'


###############################
# function
###############################
def save_original_qt_prfxpath():
    print '---------------- Saving original qt_prfxpath ---------------------'
    global ORIGINAL_QMAKE_QT_PRFXPATH
    qmake_executable_path = bldinstallercommon.locate_executable(QT_SOURCE_DIR, 'qmake' + bldinstallercommon.get_executable_suffix())
    ORIGINAL_QMAKE_QT_PRFXPATH = patch_qmake_qt_key.fetch_key(os.path.normpath(qmake_executable_path), 'qt_prfxpath')
    print ' ===> Original qt_prfxpath: ' + ORIGINAL_QMAKE_QT_PRFXPATH
    if not ORIGINAL_QMAKE_QT_PRFXPATH:
        print '*** Could not find original qt_prfxpath from qmake executable?!'
        print '*** Abort!'
        sys.exit(-1)


###############################
# function
###############################
#def restore_qt_prfxpath():
#    print '---------------- Restoring original qt_prfxpath ---------------------'
#    qmake_executable_path = bldinstallercommon.locate_executable(MAKE_INSTALL_ROOT_DIR, 'qmake' + bldinstallercommon.get_executable_suffix())
#    print ' ===> Patching: ' + qmake_executable_path
#    patch_qmake_qt_key.replace_key(qmake_executable_path, 'qt_prfxpath', ORIGINAL_QMAKE_QT_PRFXPATH)


###############################
# function
###############################
def replace_build_paths(path_to_checked):
    print '---------------- Replacing build paths ---------------------'
    for root, dirs, files in os.walk(path_to_checked):
        for name in files:
            if name.endswith('.prl') or name.endswith('.la') or name.endswith('.pc'):
                path = os.path.join(root, name)
                print '---> Replacing build path in: ' + path
                print '--->         String to match: ' + QT_SOURCE_DIR
                print '--->             Replacement: ' + ORIGINAL_QMAKE_QT_PRFXPATH
                for line in fileinput.FileInput(path,inplace=1):
                   line = line.replace(QT_SOURCE_DIR, ORIGINAL_QMAKE_QT_PRFXPATH)

###############################
# function
###############################
def clean_up(install_dir):
    print '---------- Cleaning unnecessary files from ' + install_dir + '----------'
    file_list = ['Makefile', '.o', '.moc', '.pro', '.pri', '.init-repository', '.cpp', '.h', '.gitignore', '.qmlproject']
    for root, dirs, files in os.walk(install_dir):
        for name in files:
            if name in file_list:
                path = os.path.join(root, name)
                print '    ---> Deleting file: ' + name
                os.remove(path)

    #TODO: At the moment, it seems that installing to default location is necessary
    #to be able to install to INSTALL_ROOT, so remove here the installation from default location
    if bldinstallercommon.is_linux_platform() or bldinstallercommon.is_mac_platform():
        default_install_dir = '/usr/local/Qt-5.0.0'
        if os.path.exists(default_install_dir):
            print '    Removing /usr/local/Qt-5.0.0 on mac/linux..'
            bldinstallercommon.remove_tree(default_install_dir)


###############################
# function
###############################
def restore_pri_files():
    print '---------------- Restoring original .pri files ---------------------'
    pri_dir_all = WORK_DIR + os.sep + ALL_MODULE_PRI_FILES_DIR_NAME
    bin_pri_path = ''
    #restore files to all install location

    print '  set bin_pri_path'
    if bldinstallercommon.is_win_platform():
        bin_pri_path = '\qt5_workdir\w\s\qtbase\modules'
        print '    on win path to installed pri files: ' + bin_pri_path
    if bldinstallercommon.is_linux_platform():
        bin_pri_path = '/usr/local/Qt-5.0.0/mkspecs/modules'
        print '    on linux path to installed pri files: ' + bin_pri_path
#    if bldinstallercommon.is_mac_platform():   #TODO
#        print '    mac'


    #restore pri files to each submodule and to the chunk of all modules
    module_pri_dir = WORK_DIR + os.sep + MODULE_PRI_FILES_DIR_NAME
    for root, dirs, files in os.walk(module_pri_dir):
        for name in files:
            if name.endswith('.pri'):
                original_pri_file_path = os.path.join(root, name)
                pri_file_name = name

                for root2, dirs2, files2 in os.walk(MAKE_INSTALL_ROOT_DIR):
                    for name2 in files2:
                        if name2 == pri_file_name:
                            installed_pri_file_path = os.path.join(root2, name2)
                            if not os.path.isdir(original_pri_file_path) and not os.path.islink(original_pri_file_path):
                                print ' Copying: ' + name
                                print '     Src:' + original_pri_file_path
                                print '     Dst:' + installed_pri_file_path
                                shutil.copy(original_pri_file_path, installed_pri_file_path)

    print '--------------------------------------------------------------------'


###############################
# function
###############################
def archive_submodules():

    print '-------------------- Archiving submodules --------------------------'

    bldinstallercommon.create_dirs(MODULE_ARCHIVE_DIR)
    for sub_dir in QT5_MODULES_LIST:
        print '---------- Archiving ' + sub_dir
        if os.path.exists(MAKE_INSTALL_ROOT_DIR + os.sep + 'submodule_install_' + sub_dir):
            cmd_args = '7z a ' + MODULE_ARCHIVE_DIR + os.sep + sub_dir + '.7z ' + 'submodule_install_' + sub_dir
            bldinstallercommon.do_execute_sub_process_get_std_out(cmd_args.split(' '), MAKE_INSTALL_ROOT_DIR, True, True)
        else:
            print MAKE_INSTALL_ROOT_DIR + os.sep + 'submodule_install_' + sub_dir + ' DIRECTORY NOT FOUND\n      -> ' + sub_dir + ' not archived!'

    if os.path.exists(MAKE_INSTALL_ROOT_DIR + os.sep + MAIN_INSTALL_DIR_NAME):
        print '    Archiving all modules to archive qt5_all.7z'
        cmd_args = '7z a ' + MODULE_ARCHIVE_DIR + os.sep + 'qt5_all' + '.7z ' + MAIN_INSTALL_DIR_NAME
        bldinstallercommon.do_execute_sub_process_get_std_out(cmd_args.split(' '), MAKE_INSTALL_ROOT_DIR, True, True)

    print '---------------------------------------------------------------------'


###############################
# function
###############################
def parse_cmd_line():
    global CONFIGURE_OPTIONS
    global QT_SRC_PACKAGE_URL
    global DEVEL_MODE
    global FORCE_MAKE
    global RUN_RPATH
    global MAKE_CMD
    global MAKE_INSTALL_CMD

    print '------------- Parsing commandline arguments ------------------------'
    arg_count = len(sys.argv)
    if arg_count < 2:
        print '*** Error! Insufficient arguments given!'
        print ''
        print 'Example: python -u mkqt5bld.py src_url=qt-everywhere-opensource-src-5.0.0.zip force_make [make_cmd=mingw32-make]'
        sys.exit(-1)

    #Parse command line options
    for item in sys.argv[1:]:
        print '    Argument: ' + item

        if item.find('src_url') >= 0:       #url for the sources
            values = item.split('=')
            QT_SRC_PACKAGE_URL = values[1]
            print '        source url set.'
        if item.find('devel_mode') >= 0:    #is using development mode
            DEVEL_MODE = 1
            CONFIGURE_OPTIONS += ' -nomake examples'
            print '        devel mode set to true.'
        if item.find('use_prefix') >= 0:    #prefix for configure
            values = item.split('=')
            CONFIGURE_OPTIONS += ' -prefix ' + values[1]
            print '        -prefix added to configure line.'
        if item.find('force_make') >= 0:    #set force make (-i option for make)
            FORCE_MAKE = 1
            print '        using force make (ignoring errors).'
        if item.find('run_rpath') >= 0:    #set to run rpath
            RUN_RPATH = 1
            print '        enabling chrpath execution.'
        if item.find('make_cmd') >= 0:    #set make command, if not set make/nmake is used
            values = item.split('=')
            if values[1] != '':
                MAKE_CMD = values[1]
                MAKE_INSTALL_CMD = values[1] + ' install'
            print '        using command: ' + MAKE_CMD + ' for making and ' + MAKE_INSTALL_CMD + ' for installing'

    print 'Qt source dir set to: ' + QT_SRC_PACKAGE_URL

    return True


###############################
# function
###############################
def main():
    # init
    bldinstallercommon.init_common_module(SCRIPT_ROOT_DIR)
    # parse cmd line
    parse_cmd_line()
    # init
    init_mkqt5bld()
    # create work dir
    bldinstallercommon.create_dirs(WORK_DIR)
    # fetch src package (or create?)
    fetch_src_package()
    # extract src package
    extract_src_package()
    # build
    build_qt()
    # save original qt_prfxpath in qmake executable
    save_original_qt_prfxpath()
    # install
    install_qt()
    # patch rpaths
    #patch_rpaths()
    # restore qt_prfxpath in qmake executable
    #restore_qt_prfxpath()
    #cleanup files that are not needed in binary packages
    clean_up(MAKE_INSTALL_ROOT_DIR)
    # replace build directory paths in installed files
    replace_build_paths(MAKE_INSTALL_ROOT_DIR)
    # restore pri files
    restore_pri_files()
    # archive each submodule
    archive_submodules()

###############################
# function
###############################
main()


