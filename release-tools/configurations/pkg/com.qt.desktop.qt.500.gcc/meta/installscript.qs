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

