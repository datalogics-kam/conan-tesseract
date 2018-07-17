from dl_conan_build_tools.tasks import build, conan
from invoke import Collection
from invoke.tasks import Task

# It's possible to define some per-project tasks here as well

# Make a top level task list for this module
tasks = [v for v in locals().values() if isinstance(v, Task)]

# And include at the top level, all the build tasks from dl_conan_build_tools
tasks.extend(build.tasks)

# Bring in the conan tasks from the module of that name, plus all the top level tasks
ns = Collection(conan, *tasks)

# Echo things by default
ns.configure({'run': {'echo': 'true'}})
