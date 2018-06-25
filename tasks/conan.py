from collections import OrderedDict
from conans import tools
from conans.client.conan_api import Conan
from conans.client.store.localdb import LocalDB
from conans.model.ref import ConanFileReference
from contextlib import contextmanager
from cpt.ci_manager import CIManager
from cpt.packager import ConanMultiPackager
from cpt.printer import Printer
from invoke import task
import getpass
import inspect
import io
import json
import os
import re
import requests
import shutil
import sys
import tempfile

DL_ARTIFACTORY = 'http://artifactory.dlogics.com:8081/artifactory'
PROFILE_URL = f'{DL_ARTIFACTORY}/webapp/#/profile'
UPLOAD_REMOTE = 'conan-local'
UPLOAD_DEPENDENCIES_REMOTE = 'conan-ext'
REDIRECT_REMOTE = 'conan-redirect'
STABLE_BRANCH_PATTERN = 'dl/stable/.*'
STABLE_USERNAME = 'datalogics'
PASSWORD_MESSAGE=f'''\
Your encrypted password is required to log you in to Artifactory.

To get your encrypted password, go to:

{PROFILE_URL}

Enter your password to unlock the page, then click the copy button next to
Encrypted Password.'''

if sys.platform == "win32":
    multi_default = True # Visual studio
else:
    multi_default = False # default: Unix Makefiles

remotes = OrderedDict([
    ('conan-redirect', f'{DL_ARTIFACTORY}/api/conan/conan-redirect'),
    ('conan-ext', f'{DL_ARTIFACTORY}/api/conan/conan-ext'),
    ('conan-local', f'{DL_ARTIFACTORY}/api/conan/conan-local'),
    ('conan-center', 'https://conan.bintray.com'),
    ('bincrafters', 'https://api.bintray.com/conan/bincrafters/public-conan')
])

def get_remotes(conan_api):
    urls = dict()

    for remote in conan_api.remote_list():
        urls[remote.url] = remote.name

    return urls

@task
def setup_remotes(ctx):
    """Ensure that the necessary Conan remotes are present in order to download dependencies."""
    conan_api, _, user_io = Conan.factory(interactive=False)
    urls = get_remotes(conan_api)

    insert_loc = 0
    for remote, url in remotes.items():
        if url in urls:
            old_remote = urls[url]
            if old_remote != remote:
                user_io.out.info(f'renaming {old_remote} to {remote}')
                conan_api.remote_rename(old_remote, remote)
        user_io.out.info(f'adding remote {remote} as {url}')
        conan_api.remote_add(remote, url, insert=insert_loc, force=True)
        insert_loc += 1

@task(pre=[setup_remotes])
def login(ctx, username=None):
    """Log the user into Artifactory for all the remotes that are local to Datalogics."""
    token = None
    conan_api, client_cache, user_io = Conan.factory(interactive=False)
    localdb = LocalDB(client_cache.localdb)

    for remote in conan_api.remote_list():
        if not remote.url.startswith(DL_ARTIFACTORY):
            continue

        # Check if logged in already
        name, old_token = localdb.get_login(remote.url)
        if old_token is not None:
            continue

        if token is None:
            user_io.out.highlight(PASSWORD_MESSAGE)
            if username is None:
                default_username = getpass.getuser()
                username = input(f"Enter your Artifactory username [{default_username}]: ") or default_username

            password = getpass.getpass(f'Enter encrypted password for {username} on {DL_ARTIFACTORY}: ')
            # Technically, could just use the password, but this ensures the password is correct
            r = requests.get(f'{DL_ARTIFACTORY}/api/security/encryptedPassword',
                             auth=(username, password))
            r.raise_for_status()
            token = r.text

        conan_api.authenticate(username, token, remote.name)

@task
def install(ctx, multi=multi_default, build_type="Debug"):
    """Install the requirements For this project, downloading them from remotes as necessary."""

    install_opts = " --build missing"

    if sys.platform == 'linux':
        install_opts = install_opts + " --profile devtoolset-7 --build boost_build"

    # see http://docs.conan.io/en/latest/integrations/cmake/cmake_multi_generator.html
    if multi:
        env = {"DL_CONAN_GENERATE_CMAKE_MULTI": "True"}
        ctx.run(f'conan install . --install-folder build -s build_type=Release{install_opts}', env=env)
        ctx.run(f'conan install . --install-folder build -s build_type=Debug{install_opts}', env=env)
    else:
        ctx.run(f'conan install . --install-folder build -s build_type={build_type}{install_opts}')

def package_username():
    ci_manager = CIManager(Printer())
    branch = ci_manager.get_branch()

    prog = re.compile(STABLE_BRANCH_PATTERN)
    if branch and prog.match(branch):
        return STABLE_USERNAME

    return getpass.getuser()

def tools_deps_env_info():
    conan_api, client_cache, user_io = Conan.factory(interactive=False)
    conan_api.install('build_tools', install_folder='build_tools/build')
    with open("build_tools/build/conanbuildinfo.json") as f:
        conanbuildinfo = json.load(f)
    deps_env_info = conanbuildinfo['deps_env_info']
    return deps_env_info

def get_builder(force_upload, username, conan_api=None, client_cache=None):
    """Create a ConanMultiPackager with the desired options"""
    builder = ConanMultiPackager(username=username,
                                 channel="testing", # if not the stable branch
                                 skip_check_credentials=True,
                                 args=[],
                                 build_policy="missing",
                                 archs=["x86_64"],
                                 apple_clang_versions=['9.1'],
                                 visual_versions=[12],
                                 gcc_versions=["7"],
                                 stable_branch_pattern=STABLE_BRANCH_PATTERN,
                                 upload_only_when_stable=not force_upload,
                                 upload=(remotes[UPLOAD_REMOTE], True, UPLOAD_REMOTE),
                                 conan_api=conan_api,
                                 client_cache=client_cache)
    return builder

@task(help={
    'username': "Username for the package reference. Defaults to the current user, but to 'datalogics' on release branches",
    'force-upload': 'Upload to Artifactory, even if not on a release branch'
})
def package(ctx, username=package_username(), force_upload=False):
    """Create all the builds of this project. If the current branch starts with 'release-', uploads to Artifactory."""
    os.environ["CONAN_PIP_USE_SUDO"] = 'False'
    os.environ["CONAN_NON_INTERACTIVE"] = 'True'
    builder = get_builder(force_upload, username)
    builder.add_common_builds()
    base_profile_name=None
    if sys.platform == 'linux':
        base_profile_name="devtoolset-7"

    with tools.environment_append(tools_deps_env_info()):
        conan_api, client_cache, user_io = Conan.factory(interactive=False)
        leptonica = ConanFileReference.loads('leptonica/1.76.0@bincrafters/stable')
        for settings, options, env_vars, build_requires, reference in builder.items:
            with tempfile.TemporaryDirectory() as tmpdir:
                conan_api.install_reference(reference=leptonica,
                                            settings=options_list(settings),
                                            options=options_list(options),
                                            env=env_vars,
                                            build=[leptonica.name, "missing"],
                                            profile_name=base_profile_name,
                                            install_folder=tmpdir)

        builder.run(base_profile_name=base_profile_name)

def remove_remote_refs(conan_api):
    for ref in conan_api.remote_list_ref():
        conan_api.remote_remove_ref(ref)

def check_long_paths(user_io):
    if sys.platform == 'win32':
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            'SYSTEM\\CurrentControlSet\\Control\\FileSystem',
                            access=winreg.KEY_READ) as key:
            try:
                long_paths_enabled, key_type = winreg.QueryValueEx(key, 'LongPathsEnabled')
            except FileNotFoundError:
                long_paths_enabled = False

            if not long_paths_enabled:
                user_io.out.warn("It is strongly recommended to enable long paths in Windows")
                user_io.out.warn("See the LongPathsEnabled.reg file in the tasks directory")

@contextmanager
def temporary_user_home_api():
    """Create a temporary copy of CONAN_USER_HOME, and use that for the enclosed statements. The temporary copy
    has the same configuration as the current CONAN_USER_HOME, but not the data directory. Also, all remote refs will
    be removed, so the choice of package resolution will be updated"""
    original_user_home = os.path.abspath(os.getenv('CONAN_USER_HOME', os.path.expanduser('~/.conan')))
    with tempfile.TemporaryDirectory() as tmpdir:
        new_user_home = os.path.join(tmpdir, '.conan')
        shutil.copytree(original_user_home, new_user_home, ignore=shutil.ignore_patterns('data', '.conan.db'))
        os.chmod(new_user_home, 0o777)  # Workaround for strange Windows permission problems

        # Make the API, so that the LocalDB points to the user directory
        conan_api, client_cache, user_io = Conan.factory(interactive=False)
        # And now change the conan_folder so that the rest of the data goes into the tmp dir
        client_cache.conan_folder = new_user_home
        # This is kind of rude, but it'd be nice to be able to inject a ClientCache with the characteristics I want
        client_cache._store_folder = os.path.join(new_user_home, 'data')

        with tools.environment_append({'CONAN_USER_HOME': tmpdir,
                                       'CONAN_USER_HOME_SHORT': 'None'}):

            check_long_paths(user_io)
            remove_remote_refs(conan_api)

            yield conan_api, client_cache, user_io

        del conan_api, client_cache, user_io

def dependencies(conan_api):
    deps_graph, _ = conan_api.info('.', profile_name='default', build=['never'])
    return (node.conanfile for node in deps_graph.nodes if node != deps_graph.root)

def stable_non_local_dependencies(conan_api):
    for dependency in dependencies(conan_api):
        if dependency.user == 'datalogics' or dependency.user == getpass.getuser():
            continue

        if dependency.channel != 'stable':
            continue

        yield dependency

@task(pre=[login, install])
def upload_dependencies(ctx):
    upload_dependencies_internal()

def upload_dependencies_internal(conan_api=None):
    if conan_api is None:
        conan_api, _, _ = Conan.factory(interactive=False)

    remote_refs = conan_api.remote_list_ref()

    for conanfile in stable_non_local_dependencies(conan_api):
        if hasattr(conanfile, 'alias'):
            # It's an alias, so skip it
            continue

        ref = repr(conanfile) # converting the ConanFile object to a string this way gives the reference

        dl_ref = ConanFileReference(conanfile.name, conanfile.version, STABLE_USERNAME, conanfile.channel)
        conan_api.copy(ref, f"{dl_ref.user}/{dl_ref.channel}", force=True, packages=True)
        conan_api.upload(str(dl_ref),
                         remote=UPLOAD_DEPENDENCIES_REMOTE,
                         confirm=True,
                         force=True,
                         all_packages=True)
        if ref in remote_refs:
            conan_api.remote_update_ref(ref, remote=REDIRECT_REMOTE)

        # Delete the local caches
        conan_api.remove(ref, force=True)

        # create an alias
        conan_api.export_alias(ref, str(dl_ref))
        conan_api.upload(ref, remote=REDIRECT_REMOTE, confirm=True)

@contextmanager
def conan_redirect_removed(conan_api=None):
    if conan_api is None:
        conan_api, _, _ = Conan.factory(interactive=False)
    if REDIRECT_REMOTE in [remote.name for remote in conan_api.remote_list()]:
        conan_api.remote_remove(REDIRECT_REMOTE)

    yield

    conan_api.remote_add(REDIRECT_REMOTE, remotes[REDIRECT_REMOTE])

def options_list(d):
    return [f'{k}={v}' for k, v in d.items()]

def install_all_configurations(conan_api, client_cache):
    builder = get_builder(False, getpass.getuser(), conan_api=conan_api, client_cache=client_cache)
    builder.add_common_builds()
    base_profile_name = None
    if sys.platform == 'linux':
        base_profile_name = "devtoolset-7"
    # Enumerate over the settings that we would have built for packaging
    with conan_redirect_removed(conan_api):
        for settings, options, env_vars, build_requires, reference in builder.items:
            with tempfile.TemporaryDirectory() as tmpdir:
                conan_api.install(path=".",
                                  settings=options_list(settings),
                                  options=options_list(options),
                                  env=env_vars,
                                  build=["missing"],
                                  profile_name=base_profile_name,
                                  generators=False,
                                  install_folder=tmpdir)

@task(pre=[login])
def copy_dependencies(ctx):
    """Upload stable dependencies to the conan-ext repository."""
    with temporary_user_home_api() as api:
        conan_api, client_cache, user_io = api
        install_all_configurations(conan_api, client_cache)
        upload_dependencies_internal(conan_api)
