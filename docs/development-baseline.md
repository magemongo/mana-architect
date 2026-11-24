# Offline development baseline

## Baseline of 2026-11-23

Tested commit `43dbec1603522efba1908c00eb4bf5496190df1b` on Windows 11
(build 26300) with Python 3.15.2, pytest 9.2.0, SQLAlchemy 2.1.8,
python-dotenv 1.2.4, jsonschema 4.26.0, and Streamlit 1.67.0.

The extracted archive matched 130 tracked paths. It included all 43 pytest
modules, eight fixture files, ten configuration files, the Streamlit source,
and the EDHREC reference used by a catalog test. Tests used the archived
source, copied catalogs, and temporary data. Before-and-after workspace metadata
and the tracked Git diff verified the protected paths.

| Invocation | Exit | Wall time | Outcome |
| --- | ---: | ---: | --- |
| Catalog import path | 0 | 0.137 s | Resolved to the scratch checkout |
| Collection | 0 | 7.633 s | 697 tests collected |
| Collection Playability Analysis (CPA), boundaries, and adversarial goldens | 0 | 3.027 s | 215 passed |
| Deck pipeline, Phase 2–3 goldens, composer, and refinement | 0 | 5.024 s | 167 passed |
| Import, classification persistence, catalog, and mocked Scryfall sync | 0 | 4.634 s | 36 passed |
| Broad suite | 0 | 16.611 s | 695 passed, 2 deselected |
| Suggestions AppTest | Timed out | 120.009 s | No pytest exit or assertion result |
| Deck Builder AppTest | Timed out | 120.009 s | No pytest exit or assertion result |

The completed pytest runs reported zero failures, errors, or skips. The
focused selections overlap with the broad run. Full-suite verification remains
open for the two AppTests below. The [CPA Phase 2 closing report](collection-playability/phase-2-mvp-closing-report.md)
previously recorded 695 passes and two deselections in a separate run.

### Known issues

| Node ID | Disposition |
| --- | --- |
| `tests/test_streamlit_app.py::test_suggestions_page_filters_by_identity_and_strategy_without_corrections` | Previously timed out on Windows. Timed out again with its own 120-second limit. |
| `tests/test_streamlit_app.py::test_builder_generates_with_selected_catalog_strategy` | Previously timed out on Windows. Timed out again with its own 120-second limit. |

An independent `socket.socketpair()` probe in the same sandbox policy stalled
in the Windows socket fallback at `accept()` and reached its 10-second limit.
This supports an environment diagnosis. Both AppTest assertion outcomes remain
unverified. Retry in a Windows runner with local socket support and outbound
access blocked.

The original 2026-11-23 evidence is in the external scratch directory
recorded in the baseline handoff. Its `results.json` records the original child
arguments, exit codes, and durations. The matching `*.log` files contain raw
output, `archive-files.txt` lists the extracted files, and `loopback.json`
and `loopback.log` record the independent socket probe.

Each historical replay or task run has its own directory. Its `inputs.json`
records the base and overlay, and `results.jsonl` records one result per gate.
Keep the matching `*.log` and any `*.timeout` files with those records.

## Run isolated validation

Run in a disposable PowerShell session with outbound access blocked. Use the
existing virtual environment. Choose a scratch root outside the repository
that stays accessible during review. The archive allowlist excludes local
collection data and contains the paths used by pytest. Extend it only for
reviewed test resources. The script checks the archive paths and catalog
import path before running tests.

Use `task` mode for routine validation. Set `$taskBase` to the revision the
task started from. `HEAD` is appropriate when it still points to that revision.
List the task's reviewed file changes under the archive allowlist, including new
files, deletions, and both paths of a rename. The script lists eligible changes
if the list is empty and work is present. Review the diff and any new file
contents before filling the list. The overlay copies the working-tree versions
of the selected files and records their hashes or deletions. Use
`historical` mode to replay the fixed 2026-11-23 revision.

The CLI can initialize or migrate SQLite even for a command such as `search`.
Real Scryfall sync writes to SQLite and the bulk cache. Streamlit Catalog
administration can rewrite `config/` beside the imported source. Keep these
operations isolated. The standalone CPA snapshot probe requires an explicit
bulk snapshot and runs separately from the routine pytest baseline.

```powershell
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$mode = 'task'
$taskBase = 'HEAD'
$reviewedPaths = @() # Add the reviewed task paths from the Git listing.
$workspace = (Get-Location).Path
$python = (Resolve-Path .\.venv\Scripts\python.exe).Path
$historicalSha = '43dbec1603522efba1908c00eb4bf5496190df1b'
$scratchRoot = [IO.Path]::GetTempPath()
$run = Join-Path $scratchRoot ("mana-baseline-" + [guid]::NewGuid())
$zip = Join-Path $run 'source.zip'
$tree = Join-Path $run 'source'
$paths = @('pyproject.toml', 'src', 'tests', 'config', 'docs/edhrec-themes.md')

if ($mode -ceq 'task') {
    $sha = git rev-parse --verify --end-of-options "$taskBase^{commit}"
    if ($LASTEXITCODE -ne 0) { throw 'Could not resolve the task base' }
    $changed = @(git diff --no-renames --name-only $sha -- @paths)
    if ($LASTEXITCODE -ne 0) { throw 'Could not list tracked task changes' }
    $added = @(git ls-files --others --exclude-standard -- @paths)
    if ($LASTEXITCODE -ne 0) { throw 'Could not list new task files' }
    $candidates = @(($changed + $added) | Sort-Object -Unique)
    if ($candidates.Count -and -not $reviewedPaths.Count) {
        $candidates | Write-Output
        throw 'Review the eligible changes and set $reviewedPaths'
    }
    if ($reviewedPaths | Where-Object { $_ -cnotin $candidates }) {
        throw 'Each reviewed path must be a changed file under the archive allowlist'
    }
} elseif ($mode -ceq 'historical') {
    if ($reviewedPaths.Count) { throw 'Clear $reviewedPaths for a historical replay' }
    $sha = $historicalSha
} else {
    throw 'Choose task or historical mode'
}

New-Item -ItemType Directory -Path $run | Out-Null
$tracked = @(git ls-tree -r --name-only $sha -- @paths)
if ($LASTEXITCODE -ne 0 -or $tracked.Count -eq 0) { throw 'Could not list baseline files' }
git archive --format=zip "--output=$zip" $sha @paths
if ($LASTEXITCODE -ne 0) { throw 'Could not archive the baseline' }
Expand-Archive -LiteralPath $zip -DestinationPath $tree
$extracted = @(Get-ChildItem -LiteralPath $tree -File -Force -Recurse | ForEach-Object {
    [IO.Path]::GetRelativePath($tree, $_.FullName).Replace('\', '/')
})
if (Compare-Object ($tracked | Sort-Object) ($extracted | Sort-Object)) {
    throw 'Extracted paths differ from the tracked allowlist'
}

$overlay = @(foreach ($path in $reviewedPaths) {
    $source = Join-Path $workspace $path
    $destination = Join-Path $tree $path
    if (Test-Path -LiteralPath $source -PathType Leaf) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $destination -Force
        [pscustomobject]@{ Path = $path; Sha256 = (Get-FileHash -LiteralPath $destination).Hash }
    } elseif ((Test-Path -LiteralPath $source) -or
        -not (Test-Path -LiteralPath $destination -PathType Leaf)) {
        throw "Could not apply reviewed file change: $path"
    } else {
        Remove-Item -LiteralPath $destination
        [pscustomobject]@{ Path = $path; Deleted = $true }
    }
})
[pscustomobject]@{ Mode = $mode; BaseSha = $sha; Overlay = $overlay } |
    ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $run 'inputs.json')
Set-Location -LiteralPath $tree

$env:PYTHONPATH = Join-Path $tree 'src'
$env:PYTHON_DOTENV_DISABLED = '1'
$env:MANA_ARCHITECT_DB_PATH = Join-Path $run 'fallback.db'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:STREAMLIT_BROWSER_GATHER_USAGE_STATS = 'false'
Remove-Item Env:\PYTEST_ADDOPTS, Env:\PYTEST_PLUGINS -ErrorAction SilentlyContinue

& $python -B -c 'from pathlib import Path; from mana_architect.catalog import DEFAULT_CATALOG_DIR; assert DEFAULT_CATALOG_DIR.resolve() == Path("config").resolve()'
if ($LASTEXITCODE -ne 0) { throw 'Catalog path is outside the scratch checkout' }
```

Run the gates in order. Use a unique `--basetemp` for each invocation. A
failed gate stops the sequence. Recheck its selection once in a fresh
temporary directory and record both outcomes. The only allowed nonzero outcome
is a 120-second timeout reported by the wrapper for either exact AppTest node
run individually. An ordinary child exit of 124 fails the gate. An allowed
timeout still leaves that test unverified.

```powershell
$pt = @('-B', '-m', 'pytest', '-c', 'pyproject.toml', '-q', '-ra',
    '--tb=short', '-p', 'no:cacheprovider')
$ui = @(
    'tests/test_streamlit_app.py::test_suggestions_page_filters_by_identity_and_strategy_without_corrections'
    'tests/test_streamlit_app.py::test_builder_generates_with_selected_catalog_strategy'
)
$cpa = @(Get-ChildItem tests -Filter 'test_collection_playability*.py' -File |
    Sort-Object Name | ForEach-Object { "tests/$($_.Name)" })
$timeout = @'
from pathlib import Path
import subprocess
import sys

try:
    result = subprocess.run([sys.executable, *sys.argv[2:]], timeout=120)
except subprocess.TimeoutExpired:
    Path(sys.argv[1]).write_text("timeout\n", encoding="utf-8")
    print("TIMEOUT after 120 seconds", file=sys.stderr)
    raise SystemExit(124)
raise SystemExit(result.returncode)
'@

function Invoke-BaselineTest {
    param([ValidatePattern('^[A-Za-z0-9_-]+$')][string]$Name, [string[]]$Selection)

    $childArguments = $pt + @('--basetemp', (Join-Path $run $Name)) + $Selection
    $timed = $Selection.Count -eq 1 -and $ui -ccontains $Selection[0]
    $log = Join-Path $run "$Name.log"
    $timeoutMarker = Join-Path $run "$Name.timeout"
    if ((Test-Path -LiteralPath $log) -or (Test-Path -LiteralPath $timeoutMarker) -or
        (Test-Path -LiteralPath (Join-Path $run $Name))) {
        throw "Invocation name already used ($Name)"
    }
    $arguments = $childArguments
    if ($timed) { $arguments = @('-B', '-c', $timeout, $timeoutMarker) + $childArguments }
    $timer = [Diagnostics.Stopwatch]::StartNew()
    & $python @arguments 2>&1 | Tee-Object -FilePath $log
    $exitCode = $LASTEXITCODE
    $timer.Stop()
    $allowedTimeout = $timed -and $exitCode -eq 124 -and
        (Test-Path -LiteralPath $timeoutMarker -PathType Leaf)
    [pscustomobject]@{
        Invocation = $Name
        Command = @($python) + $childArguments
        TimeoutSeconds = if ($timed) { 120 }
        TimedOut = $allowedTimeout
        ExitCode = $exitCode
        ElapsedSeconds = $timer.Elapsed.TotalSeconds
        Log = $log
    } | ConvertTo-Json -Compress | Add-Content -LiteralPath (Join-Path $run 'results.jsonl')
    if ($exitCode -ne 0 -and -not $allowedTimeout) {
        throw "Baseline gate failed ($Name, exit $exitCode)"
    }
}

Invoke-BaselineTest -Name collect -Selection @('--collect-only')
Invoke-BaselineTest -Name cpa -Selection ($cpa + @(
    'tests/test_ability_boundaries.py', 'tests/test_cpa_adversarial_golden.py'
))
Invoke-BaselineTest -Name deck -Selection @(
    'tests/test_deck_pipeline.py', 'tests/test_phase23_golden.py',
    'tests/test_preliminary_composer.py', 'tests/test_refinement.py'
)
Invoke-BaselineTest -Name storage -Selection @(
    'tests/test_collection_import.py', 'tests/test_classification_repository.py',
    'tests/test_catalog.py', 'tests/test_scryfall_sync.py'
)
Invoke-BaselineTest -Name broad -Selection @('--deselect', $ui[0], '--deselect', $ui[1])
Invoke-BaselineTest -Name ui-1 -Selection @($ui[0])
Invoke-BaselineTest -Name ui-2 -Selection @($ui[1])
```

Record the full commit SHA, date, platform, Python and dependency versions,
commands, exit codes, elapsed times, and outcome counts. Keep raw logs outside
the repository and include the scratch directory path in the handoff. Keep
evidence until review. Routine cleanup of task-owned scratch files needs no
separate approval. Include node IDs and short error signatures for failures and
timeouts. Record skips and deselections explicitly. Report unexpected results
before continuing implementation. If isolation fails, stop, retain the logs,
and resolve the issue before retrying in a fresh scratch directory.
