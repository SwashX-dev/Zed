# zedd, the zed CLI and (as they land) the Zed desktop applications.
{
  lib,
  python3Packages,
}:

python3Packages.buildPythonApplication {
  pname = "zed-suite";
  version = "0.1.0a1";
  pyproject = true;

  src = lib.fileset.toSource {
    root = ../..;
    fileset = lib.fileset.unions [
      ../../pyproject.toml
      ../../core
      ../../installer/zedinstaller
      ../../tests/unit
    ];
  };

  build-system = [ python3Packages.setuptools ];

  nativeCheckInputs = [ python3Packages.pytestCheckHook ];

  pythonImportsCheck = [
    "zedcore.cli"
    "zedcore.daemon"
  ];

  meta = {
    description = "Zed system management: zedd and the zed command";
    homepage = "https://github.com/SwashX-dev/Zed";
    license = lib.licenses.mit;
    mainProgram = "zed";
    platforms = lib.platforms.linux;
  };
}
