import glob
import os
import shutil
import sys

from conans.client import tools
from invoke import Collection, task
from invoke.tasks import Task, call
from . import conan

if sys.platform == 'win32':
    activate_command = os.path.abspath('build/activate.bat')
    default_generator = 'Visual Studio 12 2013 Win64'
else:
    activate_command = 'source ' + os.path.abspath('build/activate.sh')
    default_generator='Unix Makefiles'

@task
def cmake(ctx, generator=default_generator):
    """Run CMake to generate the build environment."""
    if not os.path.exists('build'):
        os.mkdir('build')
    with ctx.prefix(activate_command):
        with ctx.cd('build'):
            ctx.run(f'cmake ../src -G "{generator}"')

@task
def distclean(ctx):
    """Clean up the project to its pristine distribution state. Undoes the effects of bootstrap."""
    if os.path.exists('build'):
        print ("Removing build")
        shutil.rmtree('build')
    if os.path.exists('test_package/build'):
        print ("Removing test_package/build")
        shutil.rmtree('test_package/build')

@task
def clean(ctx):
    """Clean up everything built by the project. Undoes the effects of build."""
    with ctx.prefix(activate_command):
        if os.path.exists(os.path.join("build", "conanbuildinfo_multi.cmake")):
            ctx.run('cmake --build build --target clean --config Debug')
            ctx.run('cmake --build build --target clean --config Release')
        else:
            ctx.run('cmake --build build --target clean')

@task
def test(ctx, config=None, parallel=True):
    """Run the tests associated with the project."""
    options=""
    if os.path.exists(os.path.join("build", "conanbuildinfo_multi.cmake")):
        options += f" -C {config or 'Debug'}"
    if parallel:
        options += f" -j {tools.cpu_count()}"
    with ctx.prefix(activate_command):
        with ctx.cd('build'):
            ctx.run(f'ctest{options}')

def cmake_generator():
    with open(os.path.join("build", "CMakeCache.txt")) as cache:
        for line in cache:
            line = line.strip()
            if line.startswith("CMAKE_GENERATOR:INTERNAL="):
                return line.split('=')[1]

    return None

@task
def build(ctx, config=None, parallel=True):
    """Build the project."""
    options=" --build build"
    build_system_options=""
    if parallel:
        generator = cmake_generator()
        if "Makefiles" in generator and "NMake" not in generator:
            build_system_options += f" -- -j{tools.cpu_count()}"
        elif "Visual Studio" in generator:
            build_system_options += f" -- /m:{tools.cpu_count()}"

    if os.path.exists(os.path.join("build", "conanbuildinfo_multi.cmake")):
        options += f" --config {config or 'Debug'}"
    with ctx.prefix(activate_command):
        ctx.run(f'cmake{options}{build_system_options}')

@task(pre=[conan.login, conan.install, cmake])
def bootstrap(ctx):
    """Bring the project to a buildable state, by doing a Conan install and running CMake."""
    pass

if sys.platform == 'darwin':
    @task(pre=[conan.login, call(conan.install, multi=True), call(cmake, generator="Xcode")])
    def bootstrap_xcode(ctx):
        """Create an Xcode project to build with, by doing a Conan install and running CMake."""
        pass

    @task
    def vscode(ctx):
        """Start Visual Studio Code with CMake in the path."""
        with ctx.prefix(activate_command):
            ctx.run(f'open /Applications/Visual\ Studio\ Code.app')

# Find all the tasks listed above
tasks = [v for v in locals().values() if isinstance(v, Task)]

# Construct the root namespace of tasks, adding the ones from the conan module
ns = Collection(conan, *tasks)

# Echo things by default
ns.configure({'run': {'echo': 'true'}})
