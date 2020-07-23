#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import string
import sys
import os

import venv
import subprocess
import platform
import argparse

if sys.version_info[:2] < (3, 6):
    # Don't allow anything but Python 3.6 or higher
    raise SystemError("Only Python 3.6+ is allowed")

HERE = os.path.dirname(os.path.abspath(__file__))
join = os.path.join
lower_node = platform.node().split('.')[0].lower().replace(' ', '-')
# See https://stackoverflow.com/a/10839538, but use ASCII letters
allowed = string.digits + string.ascii_letters + '-_'
HOME_DIR = join(HERE, 'python-env-%s' % ''.join(filter(allowed.__contains__, lower_node)))


def install_project_requirements(output_route):
    """
    Install the project's required modules via pip-tools.
    """
    print('Checking required packages are installed...')

    activation_path = HOME_DIR
    execut = ''
    if windows():
        execut = '.exe'
        activation_path = os.path.join(activation_path, 'Scripts')
    else:
        activation_path = os.path.join(activation_path, 'bin')

    try:
        print('Update pip ... ')
        # update pip so the other steps won't fail with a warning to update pip
        # Also, install pip-tools for better dependency management
        subprocess.check_call([os.path.join(activation_path, 'python' + execut),
                               '-m', 'pip', 'install', '--upgrade', 'pip', 'pip-tools',
                               'wheel'],
                              stdout=output_route, stderr=subprocess.STDOUT)
        pip_compile_cmd = os.path.join(activation_path, 'pip-compile' + execut)
        pip_sync_cmd = os.path.join(activation_path, 'pip-sync' + execut)
        print('Installing / Refreshing required packages... ')
        artifactory_url = 'http://artifactory.dlogics.com:8081/artifactory'
        index_url = artifactory_url + '/api/pypi/pypi/simple'
        artifactory = 'artifactory.dlogics.com'
        print('Dependency resolution...')
        # Avoid PEP 517. This gets around a problem with system_site_packages,
        # pip >= 19.0.0, and older setuptools.
        # See: https://github.com/pypa/pip/issues/6264#issuecomment-470498695
        # in pip-tools, this is the --no-build-isolation option
        subprocess.check_call([pip_compile_cmd, '--no-build-isolation', '--upgrade', '-i',
                               index_url, '--trusted-host',
                               artifactory],
                              stdout=output_route, stderr=subprocess.STDOUT)
        print('Installing/upgrading packages...')
        subprocess.check_call([pip_sync_cmd, '-i',
                               index_url, '--trusted-host',
                               artifactory],
                              stdout=output_route, stderr=subprocess.STDOUT)

    except subprocess.CalledProcessError:
        print('ERROR: Could not install required packages using ', pip_compile_cmd, ' and ', pip_sync_cmd)
        raise
    except PermissionError:
        print('ERROR: Could not run pip-tools due to permission error', activation_path)
        raise

    print('Packages up to date...')
    activate_cmd = (f' . .{HOME_DIR}/bin/activate\n' if not windows() else
                    f' {HOME_DIR}\\Scripts\\activate.bat\n')
    print('\n Now activate the virtual environment with:\n    ' + activate_cmd)


def main():
    parser = argparse.ArgumentParser(description='Virtual environment setup script')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Show package installation output')
    parser.add_argument('--env-name', action='store_true',
                        help='Print the environment name and exit')
    parser.add_argument('--env-path', action='store_true',
                        help='Print the path to the programs in the environment and exit')
    opts = parser.parse_args()

    output_route = None if opts.verbose else subprocess.DEVNULL

    if opts.env_name:
        print(HOME_DIR)
        return

    if opts.env_path:
        scripts_or_bin = 'Scripts' if windows() else 'bin'
        print(os.path.join(HERE, HOME_DIR, scripts_or_bin))
        return

    print('Creating virtualenv ', HOME_DIR)
    venv.create(HOME_DIR, system_site_packages=False, symlinks=True, with_pip=True)

    install_project_requirements(output_route)


def windows():
    'returns True on Windows platforms'
    return platform.system() == 'Windows'


if __name__ == '__main__':
    sys.exit(main())
