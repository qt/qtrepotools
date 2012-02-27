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

"""Scripts to generate SDK installer based on open source InstallerFramework"""
import ConfigParser
import os
import shutil
import sys
from time import gmtime, strftime
import urllib
import bldinstallercommon
import bld_ifw_tools_impl

BUILD_TIMESTAMP         = strftime('%d-%b-%Y', gmtime())
CONFIG_COMMON           = 0
CONFIG_TARGET           = 0
PLATFORM_IDENTIFIER     = ''
CONFIG_NAME             = ''
SCRIPT_ROOT_DIR         = os.getcwd()
GENERAL_TAG_SUBST_LIST  = []
CONFIGURATIONS_DIR      = 'configurations'
COMMON_CONFIG_NAME      = 'common'
REPO_OUTPUT_DIR         = os.path.normpath(SCRIPT_ROOT_DIR + os.sep + 'repository')
SDK_VERSION_NUMBER      = ''
PACKAGES_DIR_NAME       = ''
PACKAGES_FULL_PATH_SRC  = ''
PACKAGES_FULL_PATH_DST  = ''
ROOT_COMPONENT_NAME     = ''
CONFIG_XML_TARGET_DIR   = ''
PACKAGES_NAMESPACE      = ''
IFW_TOOLS_DIR           = ''
ARCHIVEGEN_TOOL         = ''
BINARYCREATOR_TOOL      = ''
INSTALLERBASE_TOOL      = ''
REPOGEN_TOOL            = ''
SDK_NAME_ROOT           = ''
SDK_NAME                = ''
DEBUG_RPATH             = False
DUMP_CONFIG             = False
# force development mode as default. Change to False if using pre-built package
DEVELOPMENT_MODE        = True
OFFLINE_MODE            = False
TESTCLIENT_MODE         = False

IFW_DOWNLOADABLE_ARCHIVE_NAMES_TAG  = '%IFW_DOWNLOADABLE_ARCHIVE_NAMES%'
TARGET_INSTALL_DIR_NAME_TAG         = '%TARGET_INSTALL_DIR%'
PACKAGE_DEFAULT_TAG                 = '%PACKAGE_DEFAULT_TAG%'
SDK_VERSION_NUM_TAG                 = '%SDK_VERSION_NUM%'
UPDATE_REPOSITORY_URL_TAG           = '%UPDATE_REPOSITORY_URL%'
PACKAGE_CREATION_DATE_TAG           = '%PACKAGE_CREATION_DATE%'

##############################################################
# Start
##############################################################
def main():
    """ Start """
    if parse_cmd_line():
        create_installer()
        sys.exit(0)
    else:
        printInfo()
        sys.exit(-1)


##############################################################
# Print usage info
##############################################################
def print_info():
    """ Print usage info """
    print ''
    print ''
    print 'Invalid number of arguments!'
    print ''
    print 'Usage: python create_installer.py <platform> <configuration_name>'
    print ''
    print 'Optional arguments:'
    print '  <offline>    Creates offline installer'
    print '  <devmode>    Builds Qt and IFW. Enabled by default. (does not download pre-build IFW)'
    print '  <testclient> Creates installer for RnD testing purposes only (different dist server used)'
    print ''


##############################################################
# Check if valid platform identifier
##############################################################
def check_platform_identifier(platform_identifier):
    """Check if given platform identifier is valid."""
    path_to_be_checked = SCRIPT_ROOT_DIR + os.sep + CONFIGURATIONS_DIR + os.sep + platform_identifier
    if os.path.exists(path_to_be_checked):
        return
    print '*** Unsupported platform identifier given: ' + platform_identifier
    sys.exit(-1)


##############################################################
# Check if valid configuration file
##############################################################
def check_configuration_file(configuration_name):
    """ Check if valid configuration file """
    path_to_be_checked = SCRIPT_ROOT_DIR + os.sep + CONFIGURATIONS_DIR + os.sep + PLATFORM_IDENTIFIER + os.sep + configuration_name
    if os.path.isfile(path_to_be_checked):
        return
    print '*** Unable to find given configuration file: ' + path_to_be_checked
    sys.exit(-1)


##############################################################
# Parse command line arguments
##############################################################
def parse_cmd_line():
    """Parse command line arguments."""
    arg_count = len(sys.argv)
    if arg_count < 3:
        return False

    global PLATFORM_IDENTIFIER
    global CONFIG_NAME
    global DEVELOPMENT_MODE
    global OFFLINE_MODE
    global TESTCLIENT_MODE

    PLATFORM_IDENTIFIER = sys.argv[1]
    CONFIG_NAME = sys.argv[2]
    check_platform_identifier(PLATFORM_IDENTIFIER)
    check_configuration_file(CONFIG_NAME)

    if(arg_count > 3):
        counter = 3
        while(counter < arg_count):
            argument = sys.argv[counter].lower()
            if 'devmode' == argument:
                DEVELOPMENT_MODE = True
            elif 'offline' == argument:
                OFFLINE_MODE = True
            elif 'testclient' == argument:
                TESTCLIENT_MODE = True
            else:
                print '*** Unsupported argument given: ' + argument
                sys.exit(-1)

            counter = counter + 1

    return True


##############################################################
# Initialize config parsers
##############################################################
def init_data():
    """Init data based on configuration files."""
    print '----------------------------------------'
    print 'Init Data'
    global CONFIG_COMMON
    global CONFIG_TARGET
    global PACKAGES_DIR_NAME
    global SDK_VERSION_NUMBER
    global SDK_NAME
    global SDK_NAME_ROOT
    global PACKAGES_NAMESPACE
    global PACKAGES_FULL_PATH_SRC
    global PACKAGES_FULL_PATH_DST
    global IFW_TOOLS_DIR

    if DEVELOPMENT_MODE:
        print '--------------------------'
        print '[Development mode enabled]'
        print '--------------------------'

    common_conf_path = SCRIPT_ROOT_DIR + os.sep + CONFIGURATIONS_DIR + os.sep + PLATFORM_IDENTIFIER + os.sep + COMMON_CONFIG_NAME
    target_conf_path = SCRIPT_ROOT_DIR + os.sep + CONFIGURATIONS_DIR + os.sep + PLATFORM_IDENTIFIER + os.sep + CONFIG_NAME
    CONFIG_COMMON = ConfigParser.ConfigParser()
    print 'Parsing: ' + common_conf_path
    CONFIG_COMMON.readfp(open(common_conf_path))
    CONFIG_TARGET = ConfigParser.ConfigParser()
    print 'Parsing: ' + target_conf_path
    CONFIG_TARGET.readfp(open(target_conf_path))

    PACKAGES_DIR_NAME   = bldinstallercommon.config_section_map(CONFIG_TARGET,'WorkingDirectories')['packages_dir']
    PACKAGES_DIR_NAME   = os.path.normpath(PACKAGES_DIR_NAME)
    SDK_VERSION_NUMBER  = bldinstallercommon.config_section_map(CONFIG_COMMON,'SdkCommon')['version']
    SDK_NAME            = bldinstallercommon.config_section_map(CONFIG_COMMON,'SdkCommon')['name']
    SDK_NAME_ROOT       = SDK_NAME
    PACKAGES_NAMESPACE  = bldinstallercommon.config_section_map(CONFIG_TARGET,'PackagesNamespace')['name']
    # if the packages directory name is absolute path, then the packages templates (or static packages)
    # can reside outside the "<script_root_dir>/configurations" folder
    # otherwise the packages templates must be under "/configurations"
    if os.path.isabs(PACKAGES_DIR_NAME):
        PACKAGES_FULL_PATH_SRC = os.path.normpath(PACKAGES_DIR_NAME)
        PACKAGES_FULL_PATH_DST = os.path.normpath(SCRIPT_ROOT_DIR + os.sep + os.path.basename(PACKAGES_DIR_NAME))
    else:
        PACKAGES_FULL_PATH_SRC = os.path.normpath(SCRIPT_ROOT_DIR + os.sep + CONFIGURATIONS_DIR + os.sep + PACKAGES_DIR_NAME)
        PACKAGES_FULL_PATH_DST = os.path.normpath(SCRIPT_ROOT_DIR + os.sep + PACKAGES_DIR_NAME)

    if OFFLINE_MODE:
        SDK_NAME = SDK_NAME + '-offline'
    else:
        SDK_NAME = SDK_NAME + '-online'

    if TESTCLIENT_MODE:
        SDK_NAME = SDK_NAME + '-RnD_testclient'

    if not DEVELOPMENT_MODE:
        tools_dir_name = bldinstallercommon.config_section_map(CONFIG_TARGET,'InstallerFrameworkTools')['name']
        IFW_TOOLS_DIR = SCRIPT_ROOT_DIR + os.sep + tools_dir_name
        IFW_TOOLS_DIR = os.path.normpath(IFW_TOOLS_DIR)

    if DUMP_CONFIG:
        bldinstallercommon.dump_config(CONFIG_COMMON, COMMON_CONFIG_NAME)
        bldinstallercommon.dump_config(CONFIG_TARGET, CONFIG_NAME)


##############################################################
# Cleanup
##############################################################
def clean_work_dirs():
    """Clean working directories."""
    print '----------------------------------------'
    print 'Cleaning environment'

    # delete "/packages"
    if os.path.exists(PACKAGES_FULL_PATH_DST):
        bldinstallercommon.remove_tree(PACKAGES_FULL_PATH_DST)
        print '-> deleted old existing directory: ' + PACKAGES_FULL_PATH_DST
    # delete "/ifw-tools"
    if os.path.exists(IFW_TOOLS_DIR):
        bldinstallercommon.remove_tree(IFW_TOOLS_DIR)
        print '-> deleted old existing directory: ' + IFW_TOOLS_DIR
    # delete "/repositories"
    if os.path.exists(REPO_OUTPUT_DIR):
        bldinstallercommon.remove_tree(REPO_OUTPUT_DIR)
        print '-> deleted old existing directory: ' + REPO_OUTPUT_DIR
    # delete "/config"
    config_dir_dest = bldinstallercommon.config_section_map(CONFIG_TARGET,'ConfigDir')['target_dir']
    config_dir_dest = os.path.normpath(SCRIPT_ROOT_DIR + os.sep + config_dir_dest)
    if os.path.exists(config_dir_dest):
        bldinstallercommon.remove_tree(config_dir_dest)
        print '-> deleted old existing directory: ' + config_dir_dest
    # delete sdk binary files
    fileList = os.listdir(SCRIPT_ROOT_DIR)
    for fname in fileList:
        if fname.startswith(SDK_NAME_ROOT):
            full_fn = SCRIPT_ROOT_DIR + os.sep + fname
            if os.path.isdir(full_fn):
                print '-> deleted ' + full_fn
                bldinstallercommon.remove_tree(full_fn)
            else:
                print '-> deleted ' + full_fn
                os.remove(full_fn)


##############################################################
# Set the config directory
##############################################################
def set_config_directory():
    """Copy config directory into correct place."""
    global CONFIG_XML_TARGET_DIR
    config_dir_template = bldinstallercommon.config_section_map(CONFIG_TARGET,'ConfigDir')['template_name']
    config_dir_template = os.path.normpath(SCRIPT_ROOT_DIR + os.sep + CONFIGURATIONS_DIR + os.sep + config_dir_template)

    config_dir_dest = bldinstallercommon.config_section_map(CONFIG_TARGET,'ConfigDir')['target_dir']
    config_dir_dest = os.path.normpath(SCRIPT_ROOT_DIR + os.sep + config_dir_dest)
    CONFIG_XML_TARGET_DIR = config_dir_dest

    if not os.path.exists(config_dir_dest):
        bldinstallercommon.create_dirs(config_dir_dest)
    bldinstallercommon.copy_tree(config_dir_template, config_dir_dest)


##############################################################
# Set the config.xml
##############################################################
def set_config_xml():
    """Copy config.xml template into correct place."""
    print '----------------------------------------'
    print 'Set config.xml'

    configxml_filename = bldinstallercommon.config_section_map(CONFIG_TARGET,'ConfigXml')['template_name']
    config_template_source = SCRIPT_ROOT_DIR + os.sep + CONFIGURATIONS_DIR + os.sep + PLATFORM_IDENTIFIER + os.sep + configxml_filename
    # if no config.xml template, we assume the "config" template dir already contains it
    if not os.path.exists(config_template_source):
        return

    # name has to be config.xml for installer-framework
    config_template_dest_dir = CONFIG_XML_TARGET_DIR
    config_template_dest = config_template_dest_dir + os.sep + 'config.xml'

    if os.path.exists(config_template_dest):
        os.remove(config_template_dest)
        print '-> deleted old existing config.xml: ' + config_template_dest
    if not os.path.exists(config_template_dest_dir):
        bldinstallercommon.create_dirs(config_template_dest_dir)
    shutil.copy(config_template_source, config_template_dest)
    print '-> copied [' + config_template_source + '] into [' + config_template_dest + ']'

    update_repository_url = bldinstallercommon.config_section_map(CONFIG_TARGET,'SdkUpdateRepository')['repository_url']

    fileslist = [config_template_dest]
    bldinstallercommon.replace_in_files(fileslist, SDK_VERSION_NUM_TAG, SDK_VERSION_NUMBER)
    bldinstallercommon.replace_in_files(fileslist, UPDATE_REPOSITORY_URL_TAG, update_repository_url)


##############################################################
# Substitute common version numbers etc., match against tags
##############################################################
def substitute_global_tags():
    """ Substitute common version numbers etc., match against tags """
    print '      ----------------------------------------'
    print '      Substituting global tags:'
    print '      %PACKAGE_CREATION_DATE% = ' + BUILD_TIMESTAMP
    print '      %SDK_VERSION_NUM%       = ' + SDK_VERSION_NUMBER

    # initialize the file list
    fileslist = []
    for directory in GENERAL_TAG_SUBST_LIST:
        for root, dirs, files in os.walk(directory):
            for name in files:
                path = os.path.join(root, name)
                fileslist.append(path)

    bldinstallercommon.replace_in_files(fileslist, SDK_VERSION_NUM_TAG, SDK_VERSION_NUMBER)
    bldinstallercommon.replace_in_files(fileslist, PACKAGE_CREATION_DATE_TAG, BUILD_TIMESTAMP)


##############################################################
# Substitute component specifig tags
##############################################################
def substitute_component_tags(tag_pair_list, meta_dir_dest):
    """ Substitute component specific tags """
    if len(tag_pair_list) == 0:
        return
    print '      ----------------------------------------'
    print '      Substituting component specific tags:'
    # initialize the file list
    fileslist = []

    for root, dirs, files in os.walk(meta_dir_dest):
        for name in files:
            path = os.path.join(root, name)
            fileslist.append(path)

    for pair in tag_pair_list:
        tag = pair[0]
        value = pair[1]
        if tag and value:
            print '      Matching [ ' + tag + ' ] and [ ' + value + ' ] in files list'
            bldinstallercommon.replace_in_files(fileslist, tag, value)
        else:
            print '      Warning! Ignoring incomplete tag pair [ ' + tag + ' ] for [ ' + value + ' ] pair'


##############################################################
# Repackage content of the installable compoment
##############################################################
def repackage_content_for_installation(install_dir, package_raw_name, rpath_target, package_strip_dirs, package_name, archive_name):
    """Repackage content into 7z archive."""
    # if no data to be installed, then just return
    if not package_raw_name:
        return
    if not package_strip_dirs:
        package_strip_dirs = '0'

    print '        +++++++++++++++++++++++++++++++++++++++++'
    print '        Repackage:             ' + package_raw_name
    print '        Location:              ' + install_dir
    print '        Dirst to be stripped:  ' + package_strip_dirs
    if rpath_target == '':
        print '        Relocate RPath:        No'
    else:
        print '        Relocate RPath into:   ' + '(' + install_dir + ') '+ rpath_target
    print ''

    if package_raw_name.endswith('.7z') and package_strip_dirs == '0' and not rpath_target:
        print '        No repackaging actions requred for the package'
        return

    # extract contents
    bldinstallercommon.extract_file(install_dir + os.sep + package_raw_name, install_dir)
    # remove old package
    os.remove(install_dir + os.sep + package_raw_name)
    # strip out unnecessary folder structure based on the configuration
    count = 0
    iterations = int(package_strip_dirs)
    while(count < iterations):
        #print 'Strip iteration: ' + str(count)
        count = count + 1
        l = os.listdir(install_dir)
        items = len(l)
        if items == 1:
            dir_name = l[0]
            os.chdir(install_dir)
            # TODO, windows hack, on windows path+filename > 255 causes error, so truncate temp path as much as possible
            temp_path_name = 'a'
            os.rename(dir_name, temp_path_name)
            bldinstallercommon.move_tree(temp_path_name, '.')
            bldinstallercommon.remove_tree(temp_path_name)
            os.chdir(SCRIPT_ROOT_DIR)
        else:
            print '*** Error: unsupported folder structure encountered, abort!'
            print '*** Found items: ' + str(items) + ' in directory: ' + install_dir
            sys.exit(-1)

    if rpath_target:
        if not rpath_target.startswith( os.sep ):
            rpath_target = os.sep + rpath_target
        if bldinstallercommon.is_linux_platform() or bldinstallercommon.is_solaris_platform():
            bldinstallercommon.handle_component_rpath(install_dir, rpath_target)

    # lastly compress the component back to .7z archive
    archive_component(package_name, archive_name)


##############################################################
# Archive installable component
##############################################################
def archive_component(package, package_archive_name):
    """Use archivegen tool to archive component."""
    full_path = os.path.normpath(PACKAGES_FULL_PATH_DST + os.sep + package + os.sep + 'data')
    content_path = full_path + os.sep + '*'
    package_path = full_path + os.sep + package_archive_name
    print '      --------------------------------------------------------------------'
    print '      Archive package: ' + package
    print '      Content from:    ' + content_path
    print '      Final archive:   ' + package_path

    saveas = os.path.normpath(PACKAGES_FULL_PATH_DST + os.sep + package + os.sep + package_archive_name)
    cmd_args = ARCHIVEGEN_TOOL + ' ' + saveas + ' .'
    bldinstallercommon.do_execute_sub_process_2(cmd_args, full_path, True)
    shutil.copy(saveas, full_path + os.sep + package_archive_name)
    os.remove(saveas)

    # remove stuff after archive creation
    ldir = os.listdir(full_path)
    for item in ldir:
        if not item == package_archive_name:
            item_full_path = full_path + os.sep + item
            if os.path.isdir(item_full_path):
                bldinstallercommon.remove_tree(item_full_path)
            else:
                os.remove(item_full_path)


##############################################################
# Create online components
##############################################################
def create_online_target_components(target_config):
    """Create installable online installer."""
    global GENERAL_TAG_SUBST_LIST
    bldinstallercommon.create_dirs(PACKAGES_FULL_PATH_DST)

    print '=================================================='
    print '= Creating online SDK components'
    print '=================================================='
    print ''
    for section in target_config.sections():
        if section.startswith(PACKAGES_NAMESPACE):
            print '--------------------------------------------------------------------------------'
            is_root_component = bldinstallercommon.safe_config_key_fetch(target_config, section, 'root_component')
            if is_root_component == 'yes':
                meta_dir_dest = PACKAGES_FULL_PATH_DST + os.sep + section + os.sep + 'meta'
                meta_dir_dest = os.path.normpath(meta_dir_dest)
                bldinstallercommon.create_dirs(meta_dir_dest)
                print '      Created:                ' + meta_dir_dest
                # Copy meta data
                metadata_content_source_root = PACKAGES_FULL_PATH_SRC + os.sep + section + os.sep + "meta"
                metadata_content_source_root = os.path.normpath(metadata_content_source_root)
                bldinstallercommon.copy_tree(metadata_content_source_root, meta_dir_dest)
                # substitute required tags
                GENERAL_TAG_SUBST_LIST.append(meta_dir_dest)
                # check for downloadableArchiveName
                archive_name = bldinstallercommon.safe_config_key_fetch(target_config, section, 'archive_name')
                package_url  = bldinstallercommon.safe_config_key_fetch(target_config, section, 'package_url')
                if len(package_url) > 0 and archive_name == '':
                    print '*** Variable [archive_name] was empty? This is required if package content is used , check config file!'
                    print '*** Abort!'
                    sys.exit(-1)
                tag_pair_list = []
                tag_pair_list.append([IFW_DOWNLOADABLE_ARCHIVE_NAME_TAG, archive_name])
                substitute_component_tags(tag_pair_list, meta_dir_dest)
                return
            else:
                continue


##############################################################
# Create offline static component
##############################################################
def create_offline_static_component(target_config, section, static_package_src):
    """Create installable offline target component from static data."""
    print '--------------------------------------------------------------------------------'
    print ' Static package: [' + section + ']'
    # Create needed dirs
    package_dest_dir = os.path.normpath(PACKAGES_FULL_PATH_DST + os.sep + section)
    bldinstallercommon.create_dirs(package_dest_dir)
    # copy static content, assumption is that content is on local machine or on
    # accessible network share
    print '      Copying static package: '   + section
    print '              Package source: '   + static_package_src
    print '              Package dest:   '   + package_dest_dir
    bldinstallercommon.copy_tree(static_package_src, package_dest_dir)
    print '      Copying static package: Done!'
    print '--------------------------------------------------------------------------------'


##############################################################
# Construct archive
##############################################################
def handle_archive(package_name, archive_uri, package_strip_dirs,
                   target_install_base, target_install_dir,
                   rpath_target, archive_name):
    """Handle single archive."""
    print '      --------------------------------------------------------------'
    print '      Handle archive:        '   + archive_name
    print '        archive_uri:         '   + archive_uri
    print '        package_strip_dirs:  '   + package_strip_dirs
    print '        target_install_base: '   + target_install_base
    print '        target_install_dir:  '   + target_install_dir
    print '        rpath_target:        '   + rpath_target
    print ''

    # sanity check
    if len(archive_uri) > 0 and not archive_name:
        print '*** Variable [archive_name] was empty? This is required if [archive_uri] is used , check config file!'
        print '*** Abort!'
        sys.exit(-1)

    # Create needed data dirs
    data_dir_dest = os.path.normpath(PACKAGES_FULL_PATH_DST + os.sep + package_name + os.sep + 'data')
    install_dir = os.path.normpath(data_dir_dest + os.sep + target_install_base + os.sep + target_install_dir)
    bldinstallercommon.create_dirs(install_dir)
    print '        Created:             ' + install_dir

    # transfer package from origin into destination
    package_raw_name     = os.path.basename(archive_uri)
    package_save_as_temp = os.path.normpath(install_dir + os.sep + os.path.basename(archive_uri))
    if archive_uri.startswith('http'):
        print '        Downloading:        ' + archive_uri
        print '               into:        ' + package_save_as_temp
        # validate url
        res = bldinstallercommon.is_content_url_valid(archive_uri)
        if not(res):
            print '*** Package URL is invalid: [' + archive_uri + ']'
            print '*** Abort!'
            sys.exit(-1)
        # start download
        urllib.urlretrieve(archive_uri, package_save_as_temp)
    else:
        data_content_source_root = os.path.normpath(PACKAGES_FULL_PATH_SRC + os.sep + package_name + os.sep + 'data')
        # try first if the uri points to absolute file path
        if os.path.isfile(archive_uri):
            print '        Copying:             ' + archive_uri
            print '           into:             ' + package_save_as_temp
            shutil.copy(archive_uri, package_save_as_temp)
        else:
            # lastly try to check if the given file exists in the templates '/data' folder
            temp = os.path.normpath(data_content_source_root + os.sep + archive_uri)
            if os.path.isfile( temp ):
                print '        Copying:             ' + temp
                print '           into:             ' + package_save_as_temp
                shutil.copy(temp, package_save_as_temp)
            else:
                print '*** Error! Unable to locate file defined by archive_uri: ' + archive_uri
                sys.exit(-1)

    # repackage content so that correct dir structure will get into the package
    repackage_content_for_installation(install_dir, package_raw_name, rpath_target, package_strip_dirs, package_name, archive_name)


##############################################################
# Generate java script code that is embedded into installscript.qs
##############################################################
def generate_downloadable_archive_list(downloadable_archive_list):
    """Generate java script code that is embedded into installscript.qs"""
    output = ''
    for item in downloadable_archive_list:
        output = output + 'component.addDownloadableArchive(\"' + item + '\");'

    temp_list = []
    temp_list.append([IFW_DOWNLOADABLE_ARCHIVE_NAMES_TAG, output])
    return temp_list


##############################################################
# Create all target components
##############################################################
def create_offline_target_components(target_config):
    """Create installable offline target components."""
    global ROOT_COMPONENT_NAME
    bldinstallercommon.create_dirs(PACKAGES_FULL_PATH_DST)

    print '=================================================='
    print '= Creating offline SDK components'
    print '=================================================='
    print ''
    for section in target_config.sections():
        if section.startswith(PACKAGES_NAMESPACE):
            # check first for top level component
            is_root_component = bldinstallercommon.safe_config_key_fetch(target_config, section, 'root_component')
            if is_root_component == 'yes':
                ROOT_COMPONENT_NAME = section
            # check if static component or not
            static_component = bldinstallercommon.safe_config_key_fetch(target_config, section, 'static_component')
            if static_component:
                create_offline_static_component(target_config, section, static_component)
                continue

            # otherwise "build" the component
            package_name            = section
            archives                = bldinstallercommon.safe_config_key_fetch(target_config, section, 'archives')
            target_install_base     = bldinstallercommon.safe_config_key_fetch(target_config, section, 'target_install_base')
            version                 = bldinstallercommon.safe_config_key_fetch(target_config, section, 'version')
            version_tag             = bldinstallercommon.safe_config_key_fetch(target_config, section, 'version_tag')
            package_default         = bldinstallercommon.safe_config_key_fetch(target_config, section, 'package_default')
            if (package_default != 'true') and (package_default != 'script'):
                package_default = 'false'

            print '--------------------------------------------------------------------------------'
            print '    '                             + package_name
            print '      Package target_install_base:   '   + target_install_base
            print '      Package version:               '   + version
            print '      Package version_tag:           '   + version_tag
            print '      Package package_default:       '   + package_default
            print '      Package archives:              '   + archives

            # create destination meta data folder
            meta_dir_dest = os.path.normpath(PACKAGES_FULL_PATH_DST + os.sep + package_name + os.sep + 'meta')
            bldinstallercommon.create_dirs(meta_dir_dest)
            print '      Created:                       ' + meta_dir_dest
            # Copy Meta data
            metadata_content_source_root = os.path.normpath(PACKAGES_FULL_PATH_SRC + os.sep + package_name + os.sep + 'meta')
            bldinstallercommon.copy_tree(metadata_content_source_root, meta_dir_dest)
            print '      Copied metadata'
            # add files into tag substitution
            GENERAL_TAG_SUBST_LIST.append(meta_dir_dest)
            # create lists for component specific tag substitutions
            component_metadata_tag_pair_list = []

            # version tag exists
            if version_tag or version:
                component_metadata_tag_pair_list.append([version_tag, version])
            # substitute default package info
            if package_default == 'true':
                component_metadata_tag_pair_list.append([PACKAGE_DEFAULT_TAG, 'true'])
            elif package_default == 'script':
                component_metadata_tag_pair_list.append([PACKAGE_DEFAULT_TAG, 'script'])
            else:
                component_metadata_tag_pair_list.append([PACKAGE_DEFAULT_TAG, 'false'])

            #target install dir substitution
            if target_install_base:
                component_metadata_tag_pair_list.append([TARGET_INSTALL_DIR_NAME_TAG, target_install_base])

            # check if package contains archives i.e. 7z packages to be included
            if archives:
                downloadable_archive_list = []
                archives_list = archives.split(',')
                for archive in archives_list:
                    archive_uri             = bldinstallercommon.config_section_map(target_config, archive)['archive_uri']
                    package_strip_dirs      = bldinstallercommon.safe_config_key_fetch(target_config, archive, 'package_strip_dirs')
                    # TODO, check if target_install_dir is needed at all
                    target_install_dir      = bldinstallercommon.safe_config_key_fetch(target_config, archive, 'target_install_dir')
                    rpath_target            = bldinstallercommon.safe_config_key_fetch(target_config, archive, 'rpath_target')
                    archive_name            = bldinstallercommon.safe_config_key_fetch(target_config, archive, 'archive_name')
                    # add downloadable archive name
                    downloadable_archive_list.append(archive_name)
                    handle_archive(package_name, archive_uri, package_strip_dirs, target_install_base, target_install_dir,
                                   rpath_target, archive_name)
                # substitute downloadable archive names in installscript.qs
                downloadableArchives_list = generate_downloadable_archive_list(downloadable_archive_list)
                substitute_component_tags(downloadableArchives_list, meta_dir_dest)

            # substitute tags
            substitute_component_tags(component_metadata_tag_pair_list, meta_dir_dest)


##############################################################
# Install Installer-Framework tools
##############################################################
def install_ifw_tools():
    """Setup Installer-Framework tools."""
    print '=================================================='
    print '= Install Installer Framework tools'
    print '=================================================='
    global ARCHIVEGEN_TOOL
    global BINARYCREATOR_TOOL
    global INSTALLERBASE_TOOL
    global REPOGEN_TOOL
    package_save_as_temp = None

    # if "devmode" mode used, then build IFW from sources
    if DEVELOPMENT_MODE:
        tools_dir_temp = bld_ifw_tools_impl.build_ifw('devmode', PLATFORM_IDENTIFIER)
        tools_bin_path = SCRIPT_ROOT_DIR + os.sep + tools_dir_temp + os.sep + 'installerbuilder' + os.sep + 'bin' + os.sep
    else:
        tools_dir_name = bldinstallercommon.config_section_map(CONFIG_TARGET,'InstallerFrameworkTools')['name']
        tools_dir_name = os.path.normpath(tools_dir_name)
        package_url = bldinstallercommon.config_section_map(CONFIG_TARGET,'InstallerFrameworkTools')['package_url']
        # create needed dirs
        bldinstallercommon.create_dirs(IFW_TOOLS_DIR)
        package_save_as_temp = IFW_TOOLS_DIR + os.sep + os.path.basename(package_url)
        package_save_as_temp = os.path.normpath(package_save_as_temp)
        print 'Source url: ' + package_url
        print 'Install dest: ' + package_save_as_temp
        # download IFW archive
        if not package_url == '':
            print 'Downloading:  ' + package_url
            res = bldinstallercommon.is_content_url_valid(package_url)
            if not(res):
                print '*** Package URL is invalid: [' + package_url + ']'
                print '*** Abort!'
                sys.exit(-1)
            urllib.urlretrieve(package_url, package_save_as_temp)
        if not (os.path.isfile(package_save_as_temp)):
            print '*** Downloading failed! Aborting!'
            sys.exit(-1)
        # extract IFW archive
        bldinstallercommon.extract_file(package_save_as_temp, IFW_TOOLS_DIR)
        os.remove(package_save_as_temp)
        dir_items = os.listdir(IFW_TOOLS_DIR)
        items = len(dir_items)
        if items == 1:
            dir_name = dir_items[0]
            os.chdir(IFW_TOOLS_DIR)
            bldinstallercommon.move_tree(dir_name, '.')
            bldinstallercommon.remove_tree(dir_name)
            os.chdir(SCRIPT_ROOT_DIR)
        else:
            print '*** Unsupported dir structure for installer-framework-tools package?!'
            print '*** Abort!'
            sys.exit(-1)

        # todo, hard coded path used...
        tools_bin_path = IFW_TOOLS_DIR + os.sep + 'installerbuilder' + os.sep + 'bin' + os.sep

    executable_suffix = bldinstallercommon.get_executable_suffix()
    ARCHIVEGEN_TOOL = tools_bin_path + 'archivegen' + executable_suffix
    BINARYCREATOR_TOOL = tools_bin_path + 'binarycreator' + executable_suffix
    INSTALLERBASE_TOOL = tools_bin_path + 'installerbase' + executable_suffix
    REPOGEN_TOOL = tools_bin_path + 'repogen' + executable_suffix
    # check
    if not (os.path.isfile(ARCHIVEGEN_TOOL)):
        print '*** Archivegen tool not found: ' + ARCHIVEGEN_TOOL
        sys.exit(-1)
    if not (os.path.isfile(BINARYCREATOR_TOOL)):
        print '*** Binarycreator tool not found: ' + BINARYCREATOR_TOOL
        sys.exit(-1)
    if not (os.path.isfile(INSTALLERBASE_TOOL)):
        print '*** Installerbase tool not found: ' + INSTALLERBASE_TOOL
        sys.exit(-1)
    if not (os.path.isfile(REPOGEN_TOOL)):
        print '*** Repogen tool not found: ' + REPOGEN_TOOL
        sys.exit(-1)

    print 'ARCHIVEGEN_TOOL: ' + ARCHIVEGEN_TOOL
    print 'BINARYCREATOR_TOOL: ' + BINARYCREATOR_TOOL
    print 'INSTALLERBASE_TOOL: ' + INSTALLERBASE_TOOL
    print 'REPOGEN_TOOL: ' + REPOGEN_TOOL


##############################################################
# Create the final installer binary
##############################################################
def create_installer_binary():
    """Create installer binary files using binarycreator tool."""
    print '=================================================='
    print '= Create installer binary'
    print '=================================================='
    global SDK_NAME

    instruction_set = bldinstallercommon.config_section_map(CONFIG_TARGET,'TargetArchitechture')['instruction_set']
    cmd_args = []
    SDK_NAME += '-' + bldinstallercommon.get_platform_suffix()
    SDK_NAME += '-' + instruction_set
    tmp = SDK_VERSION_NUMBER
    if bldinstallercommon.is_win_platform():
        tmp = SDK_VERSION_NUMBER.replace('.', '_')

    SDK_NAME = SDK_NAME + '-v' + tmp

    if bldinstallercommon.is_linux_platform():
        SDK_NAME = SDK_NAME + '.run'

    cmd_args = [BINARYCREATOR_TOOL, '-t', INSTALLERBASE_TOOL, '-v', '-p', PACKAGES_FULL_PATH_DST]
    if OFFLINE_MODE:
        # check if package exclude list should be used for offline installer
        package_exclude_list = bldinstallercommon.safe_config_key_fetch(CONFIG_TARGET, 'OfflinePackageExcludeList', 'package_list')
        package_exclude_list = package_exclude_list.replace('\n', '')
        if package_exclude_list:
            cmd_args = cmd_args + ['-e', package_exclude_list]
    cmd_args = cmd_args + ['-c', CONFIG_XML_TARGET_DIR, SDK_NAME, ROOT_COMPONENT_NAME]

    if OFFLINE_MODE:
        cmd_args = cmd_args + ['--offline-only']
        print 'Creating repository for the SDK ...'
        print '    Outputdir: ' + REPO_OUTPUT_DIR
        print '      pkg src: ' + PACKAGES_FULL_PATH_DST
        repogen_args = [REPOGEN_TOOL, '-p', PACKAGES_FULL_PATH_DST, '-c', CONFIG_XML_TARGET_DIR, REPO_OUTPUT_DIR, ROOT_COMPONENT_NAME]
        bldinstallercommon.do_execute_sub_process(repogen_args, SCRIPT_ROOT_DIR, True)
        if not os.path.exists(REPO_OUTPUT_DIR):
            print '*** Fatal error! Unable to create repository directory: ' + REPO_OUTPUT_DIR
            sys.exit(-1)

    # sanity checks
    if not os.path.exists(PACKAGES_FULL_PATH_DST):
        print '*** Fatal error! Could not find packages directory: ' + PACKAGES_FULL_PATH_DST
        sys.exit(-1)

    bldinstallercommon.do_execute_sub_process(cmd_args, SCRIPT_ROOT_DIR, True)


##############################################################
# Create the final installer binary
##############################################################
def create_mac_disk_image():
    """Create Mac disk image."""
    print '=================================================='
    print '= Create mac disk image'
    print '=================================================='

    nib_archive_name = bldinstallercommon.safe_config_key_fetch(CONFIG_TARGET, 'qtmenunib', 'package_url')
    package_save_as_folder = SCRIPT_ROOT_DIR + os.sep + SDK_NAME + '.app' + os.sep + 'Contents' + os.sep + 'Resources'
    package_save_as_temp = package_save_as_folder + os.sep + os.path.basename(nib_archive_name)
    print ' package_url: ' + nib_archive_name
    print ' save as:     ' + package_save_as_temp

    if not nib_archive_name == '':
        print '    Downloading:            ' + nib_archive_name
        print '           into:            ' + package_save_as_temp
        res = bldinstallercommon.is_content_url_valid(nib_archive_name)
        if not(res):
            print '*** Package URL is invalid: [' + nib_archive_name + ']'
            print '*** Abort!'
            sys.exit(-1)
        urllib.urlretrieve(nib_archive_name, package_save_as_temp)

    # extract contents
    bldinstallercommon.extract_file(package_save_as_temp, package_save_as_folder)

    # create disk image
    cmd_args = ['hdiutil', 'create', '-fs', 'HFS+', '-srcfolder', \
                os.path.join(SCRIPT_ROOT_DIR, SDK_NAME + '.app'), \
                '-volname', SDK_NAME, \
                os.path.join(SCRIPT_ROOT_DIR, SDK_NAME + '.dmg')]
    bldinstallercommon.do_execute_sub_process(cmd_args, SCRIPT_ROOT_DIR, True)


##############################################################
# All main build steps
##############################################################
def create_installer():
    """Installer creation main steps."""
    print ''
    print ''
    print '=================================================='
    print '= Creating SDK'
    print '=================================================='
    # init
    bldinstallercommon.init_common_module(SCRIPT_ROOT_DIR)
    # init data
    init_data()
    # clean env before starting
    clean_work_dirs()
    # set config templates
    set_config_directory()
    set_config_xml()
    # install Installer Framework tools
    install_ifw_tools()
    # create components
    if OFFLINE_MODE:
        create_offline_target_components(CONFIG_TARGET)
    else:
        create_online_target_components(CONFIG_TARGET)
    # substitute global tags
    substitute_global_tags()
    # create the installer binary
    create_installer_binary()
    # for mac we need some extra work
    if bldinstallercommon.is_mac_platform():
        create_mac_disk_image()


##############################################################
# Start build process
##############################################################
main()
