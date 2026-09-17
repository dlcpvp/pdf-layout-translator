"""Create/reuse an isolated runtime. Does not install into global Python/base."""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def run(argv):
    return subprocess.run(argv, check=True)


def main():
    root = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group()
    group.add_argument('--env', type=Path, default=None)
    group.add_argument('--existing-python', type=Path)
    p.add_argument('--wheelhouse', type=Path, help='Install offline from this wheel directory')
    args = p.parse_args()
    if args.existing_python:
        python = args.existing_python.resolve()
        probe = subprocess.run([str(python), '-c', 'import sys,json;print(json.dumps([sys.prefix,sys.base_prefix]))'], check=True, capture_output=True, text=True)
        prefix, base = map(Path, json.loads(probe.stdout))
        if prefix == base and not (prefix/'conda-meta').is_dir():
            p.error('Existing Python must belong to a venv or dedicated Conda environment.')
        if (prefix/'conda-meta').is_dir() and ((prefix/'condabin').is_dir() or (prefix/'pkgs').is_dir()):
            p.error('Conda base is not an isolated task environment.')
    else:
        env = (args.env or root/'.venv').resolve()
        python = env/('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
        if not python.exists():
            if env.exists() and any(env.iterdir()):
                p.error('Environment path is nonempty but has no usable Python; choose another path.')
            run([sys.executable, '-m', 'venv', str(env)])
        if not (env/'pyvenv.cfg').exists():
            p.error('The requested environment is not a venv. Use --existing-python for dedicated Conda.')
    probe = subprocess.run([str(python), '-c', 'import pymupdf,PIL,fontTools;assert pymupdf.VersionBind=="1.28.2" and PIL.__version__=="11.3.0" and fontTools.__version__=="4.60.1"'], capture_output=True)
    if probe.returncode:
        cmd = [str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(root/'scripts/requirements.txt')]
        if args.wheelhouse:
            cmd += ['--no-index', '--find-links', str(args.wheelhouse.resolve())]
        run(cmd)
    run([str(python), '-X', 'utf8', str(root/'scripts/pdf_translate.py'), 'doctor'])
    print('RUNTIME_PYTHON=' + str(python))


if __name__ == '__main__':
    main()
