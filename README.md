# license-check

GitHub Action that lists the licenses of all third-party dependencies of a project, writes a report to the
run's job summary, compares it with the previous run and opens a GitHub issue when something relevant changed.

Supported project types (auto-detected):

| Type    | Detected by      | How licenses are collected |
|---------|------------------|----------------------------|
| Maven   | `pom.xml`        | `license-maven-plugin` (declared POM licenses), test/provided scope excluded |
| pnpm    | `pnpm-lock.yaml` | `pnpm licenses list --prod` (`package.json` license field) |
| npm     | `package-lock.json` | License recorded in the lockfile (v2/v3), dev dependencies excluded; nothing is installed |
| Flutter | `pubspec.lock`   | Each package's `LICENSE` file, classified by its text |
| pip     | `requirements.txt` | Installs into a virtualenv and reads each package's metadata (License-Expression, classifiers, License) |

## What it does

1. Sets up Java / Node + pnpm / Flutter / Python (can be turned off).
2. Collects every dependency with its version and license and classifies the license as
   `permissive`, `weak-copyleft`, `strong-copyleft` or `unknown` (unrecognised or not open source).
   For dual-licensed packages the most permissive option counts.
3. Downloads the report of the last successful run of the same workflow on the same branch as the baseline.
4. Writes a Markdown report to the job summary (visible under **Actions → run**) and uploads it, with
   `licenses.tsv`, as the `license-report` artifact.
5. If a dependency's license changed, or a new dependency with a copyleft or unknown license appeared, it opens an
   issue (or comments on the open one). Version bumps and new permissive dependencies are listed in the report but
   don't trigger a notification.

The first run has no baseline and never notifies. Artifacts are kept for 90 days, so the workflow needs to
run at least that often to keep its baseline.

## Usage

`.github/workflows/license-check.yml`:

```yaml
name: license-check

on:
  schedule:
    - cron: "0 6 * * 1"   # Mondays 06:00 UTC
  workflow_dispatch:

permissions:
  contents: read
  actions: read    # read the previous run's report
  issues: write    # open the notification issue

jobs:
  licenses:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: anotheria/license-check@v1
        # with:
        #   maven-settings: ${{ secrets.MAVEN_SETTINGS_XML }}   # private Maven repositories
        #   issue-assignees: some-login
```

Scheduled workflows only run on the repository's default branch.

### Several projects in one repository

Call the action once per project with its own `working-directory` and a unique `artifact-name`:

```yaml
      - uses: anotheria/license-check@v1
        with:
          working-directory: backend
          artifact-name: license-report-backend
          title: my-repo backend
      - uses: anotheria/license-check@v1
        with:
          working-directory: frontend
          artifact-name: license-report-frontend
          title: my-repo frontend
```

## Inputs

| Input | Default | Description |
|---|---|---|
| `working-directory` | `.` | Project directory |
| `type` | `auto` | `auto`, `maven`, `pnpm`, `npm`, `flutter` or `pip` |
| `setup-toolchain` | `true` | Install the toolchain; set `false` if you already did |
| `java-version` / `java-distribution` | `21` / `temurin` | |
| `maven-settings` | | Content of a `settings.xml`, pass it from a secret |
| `maven-excluded-scopes` | `test,provided` | |
| `node-version` | `22` | |
| `include-dev` | `false` | Also check devDependencies (pnpm, npm) |
| `flutter-channel` / `flutter-version` | `stable` / latest | |
| `python-version` | `3.12` | |
| `requirements-file` | `requirements.txt` | Relative to `working-directory` |
| `title` | repository name | Report title |
| `artifact-name` | `license-report` | Must be unique per check within a workflow |
| `retention-days` | `90` | |
| `notify` | `true` | Open/comment on an issue on relevant changes |
| `issue-title` | `License check: changes detected` | |
| `issue-labels` | `license-check` | Created if missing |
| `issue-assignees` | | Comma-separated logins |
| `fail-on-unknown` | `false` | Fail if any license is unknown or not open source (useful as a PR gate) |
| `github-token` | `github.token` | |

Outputs: `type`, `changed`, `unknown-count`, `copyleft-count`, `report` (path to the Markdown file).

## Limitations

This checks declared licenses (POM metadata, `package.json`, LICENSE files). It isn't a source-level audit.
Flutter classification is a text heuristic; packages that bundle several licenses (e.g. `sky_engine`) are
reported by the first one recognised. The pnpm report reflects the platform the runner uses (e.g. `linux-x64`
native packages).

## License

MIT
