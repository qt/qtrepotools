/* This file is part of the Qt SDK

*/

// constructor
function Component()
{
    if (installer.value("os") == "win")
    {
    }
    if (component.fromOnlineRepository)
    {
        //%IFW_DOWNLOADABLE_ARCHIVE_NAMES%
    }
}


checkWhetherStopProcessIsNeeded = function()
{
}

createShortcuts = function()
{
    var qtStringVersion = "%QT_VERSION%";
    // Create a batch file with the development environment
    var batchFileName = component_root_path + "/" + "bin" + "/" + "qtenv2.bat";
    var contentString = "echo off\r\n";
    contentString += "echo Setting up environment for Qt usage...\r\n";
    contentString += "set QTDIR=" + component_root_path + "\r\n";
    contentString += "set PATH=%QTDIR%\\bin;%PATH%\r\n";
    contentString += "cd /D %QTDIR%\r\n";
    contentString += "echo Remember to call vcvarsall.bat amd64 to complete environment setup!\r\n";
    // Dump batch file
    component.addOperation("AppendFile", batchFileName, contentString);

    var windir = installer.environmentVariable("WINDIR");
    if (windir == "") {
        QMessageBox["warning"]( "Error" , "Error", "Could not find windows installation directory");
        return;
    }

    var cmdLocation = windir + "\\system32\\cmd.exe";
    component.addOperation( "CreateShortcut",
                            cmdLocation,
                            "@StartMenuDir@/%QT_VERSION%/MSVC 2010/Qt " + qtStringVersion + " for Desktop (MSVC 2010).lnk",
                            "/A /Q /K " + batchFileName);
    // Assistant
    component.addOperation( "CreateShortcut",
                            component_root_path + "/bin/assistant.exe",
                            "@StartMenuDir@/%QT_VERSION%/MSVC 2010/Assistant.lnk");

    // Designer
    component.addOperation( "CreateShortcut",
                            component_root_path + "/bin/designer.exe",
                            "@StartMenuDir@/%QT_VERSION%/MSVC 2010/Designer.lnk");

    // Linguist
    component.addOperation( "CreateShortcut",
                            component_root_path + "/bin/linguist.exe",
                            "@StartMenuDir@/%QT_VERSION%/MSVC 2010/Linguist.lnk");

    // README
    //var notePadLocation = windir + "\\notepad.exe";
    //component.addOperation( "CreateShortcut",
    //                        notePadLocation,
    //                        "@StartMenuDir@/%QT_VERSION%/MSVC 2010/Linguist.lnk");

    // Examples & Demos
    //component.addOperation( "CreateShortcut",
    //                        component_root_path + "/bin/qtdemo.exe",
    //                        "@StartMenuDir@/%QT_VERSION%/MSVC 2010/Examples & Demos.lnk");
}

Component.prototype.createOperations = function()
{
    component.createOperations();

    if (installer.value("os") == "x11") {
        try {
            // patch Qt binaries
            component.addOperation( "QtPatch", "linux", installer.value("TargetDir") + "%TARGET_INSTALL_DIR%" );
        } catch( e ) {
            print( e );
        }
    }
    if (installer.value("os") == "mac") {
        try {
            // patch Qt binaries
            component.addOperation( "QtPatch", "mac", installer.value("TargetDir") + "%TARGET_INSTALL_DIR%" );
        } catch( e ) {
            print( e );
        }
    }
    if (installer.value("os") == "win") {
        try {
            // patch Qt binaries
            component.addOperation( "QtPatch", "windows", installer.value("TargetDir") + "%TARGET_INSTALL_DIR%" );

            // Create a batch file and shortcuts with the development environment
            createShortcuts();
        } catch( e ) {
            print( e );
        }
    }
}


Component.prototype.installationFinished = function()
{
    if (installer.isInstaller() && component.selected)
        {
        }
}

