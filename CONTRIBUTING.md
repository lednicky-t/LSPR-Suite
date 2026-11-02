# Contributing to LSPR Suite

This repository is the umbrella for the LSPR application suite.

## Where to make changes

- App-specific code lives in the app repositories linked as submodules.
- Shared libraries live in `packages/`.
- Suite launcher changes live in `apps/suite_launcher/`.
- Suite-wide documentation lives in `docs/`.

## App repositories

- `apps/LSPRi/eva` -> LSPRimaging evaluation
- `apps/LSPRi/acq` -> LSPRimaging acquisition (an early scaffold)
- `apps/sLSPR/acq` -> singleLSPR acquisition
- `apps/sLSPR/eva` -> singleLSPR evaluation (a placeholder: development has not started)

## Working on an app

1. Make the change in the app repository.
2. Commit and push it there.
3. Update the submodule pointer in this suite repository if needed.
4. Commit the submodule pointer update in the suite repository.

## Shared code

Use the shared packages in `packages/` for logic that is reused across apps:

- `lspr_core` for common domain and flow helpers
- `lspr_io` for file formats and HDF rules
- `lspr_ui` for shared Qt styling and UI helpers

## Notes

- This repo keeps the umbrella structure only.
- The app histories stay in their own repositories.
- Submodule updates should stay small and intentional.

## License of contributions

- By submitting a contribution you agree that it is released under the project's [MIT License](LICENSE), and that you have the right to
  submit it (it is your own work, or you may pass it on under that licence). Do not copy in code with an incompatible licence.
- Keep the copyright and license notices in files you change.
- If you add a new third-party dependency, say which licence it has in the pull request, so that [`NOTICE.md`](NOTICE.md) can be updated.
