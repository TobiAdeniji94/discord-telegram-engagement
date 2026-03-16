param(
    [switch]$VerboseOutput
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command gitleaks -ErrorAction SilentlyContinue)) {
    Write-Error "gitleaks is not installed. Install it from https://github.com/gitleaks/gitleaks/releases."
    exit 1
}

$args = @(
    "detect",
    "--source", ".",
    "--config", ".gitleaks.toml",
    "--redact",
    "--exit-code", "1"
)

if ($VerboseOutput) {
    $args += "--verbose"
}

& gitleaks @args
exit $LASTEXITCODE
