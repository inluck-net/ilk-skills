"""Read the canonical project run registry before moving its plans/runtime."""
from pathlib import Path
from ilk_paths import project_key,external_launcher_dir
from run_registry import project_status,RegistryError


def status(project):
    project=Path(project).resolve(strict=True)
    runtimes={external_launcher_dir(project_key(project)),project/'.ilk-launcher'}
    for runtime in sorted(runtimes):
        try:
            observed=project_status(runtime,project=project)
        except (RegistryError,OSError,ValueError) as exc:
            return {'busy':True,'source':'unverified-registry','runtime':str(runtime),'error':str(exc)}
        if observed['busy']:
            return dict(observed,runtime=str(runtime))
    return {'busy':False,'source':'registry'}
