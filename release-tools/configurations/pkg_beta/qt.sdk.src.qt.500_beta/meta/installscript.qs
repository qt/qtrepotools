/* This file is part of the Qt SDK

*/

// constructor
function Component()
{
    if (component.fromOnlineRepository)
    {
        // Commented line below used by the packaging scripts
        //%IFW_DOWNLOADABLE_ARCHIVE_NAMES%
    }
}


checkWhetherStopProcessIsNeeded = function()
{
}


Component.prototype.createOperations = function()
{
    component.createOperations();
    // register qt5 documentation
    // TODO, this is temporary solution fot Beta only! In final the documentation should be
    // put in separate installable component
    component.addOperation("RegisterDocumentation" , installer.value("TargetDir") + "%TARGET_INSTALL_DIR%" + "/qtdoc/doc/qch/qt.qch"));
}


Component.prototype.installationFinished = function()
{
}

