param([string]$TargetPath = 'D:\target-system', [switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$targetRoot = (Resolve-Path -LiteralPath $TargetPath).Path
$runtimeDirectory = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\data\target-runtime'))
New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
$dockerfilePath = Join-Path $runtimeDirectory 'Dockerfile.local'
$originalDockerfile = [System.IO.File]::ReadAllText((Join-Path $targetRoot 'backend\Dockerfile'))
$stages = $originalDockerfile -split 'FROM python:3\.14', 2
if ($stages.Count -ne 2) { throw 'Target Dockerfile runtime changed; review the local build adaptation' }
$runtimeStage = @'
FROM python:3.14
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /app/backend
COPY backend/pyproject.toml /tmp/target-pyproject.toml
COPY uv.lock /tmp/target-uv.lock
RUN python -c "import tomllib; from pathlib import Path; p=tomllib.loads(Path('/tmp/target-pyproject.toml').read_text()); Path('/tmp/requirements.txt').write_text(chr(10).join(p['project']['dependencies'])); lock=tomllib.loads(Path('/tmp/target-uv.lock').read_text()); Path('/tmp/constraints.txt').write_text(chr(10).join(x['name']+'=='+x['version'] for x in lock['package'] if x.get('source',{}).get('registry')))"
RUN python -m pip install --no-cache-dir --progress-bar on -r /tmp/requirements.txt -c /tmp/constraints.txt
COPY backend/scripts /app/backend/scripts
COPY backend/pyproject.toml backend/alembic.ini /app/backend/
COPY backend/app /app/backend/app
COPY --from=frontend-build /app/backend/app/frontend /app/backend/app/frontend
ENV PYTHONPATH=/app/backend
CMD ["fastapi", "run", "--workers", "4"]
'@
[System.IO.File]::WriteAllText($dockerfilePath, $stages[0]+$runtimeStage)
$overlayPath = Join-Path $runtimeDirectory 'compose.local.yml'
$overlay = @{services = @{
    backend = @{image = 'team-c-target-backend:local'; build = @{context = $targetRoot; dockerfile = $dockerfilePath};
        ports = @('127.0.0.1:8001:8000'); environment = @{FASTAPI_ENV = 'development'; PYTHONPATH = '/app/backend'; SMTP_HOST = 'mailpit'; SMTP_PORT = '1025'; SMTP_TLS = 'false'}}
    mailpit = @{image = 'axllent/mailpit'; ports = @('127.0.0.1:8026:8025')}
}}
[System.IO.File]::WriteAllText($overlayPath, ($overlay | ConvertTo-Json -Depth 8))
$composeArgs = @('compose', '--project-name', 'team-c-target', '--project-directory', $targetRoot,
    '--env-file', (Join-Path $targetRoot '.env'), '-f', (Join-Path $targetRoot 'compose.yml'), '-f', $overlayPath)
function Invoke-TargetCompose {
    param([string[]]$Arguments)
    & docker @composeArgs @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Target Docker Compose command failed (exit $LASTEXITCODE)" }
}
if (-not $SkipBuild) { Invoke-TargetCompose @('build', 'backend') }
Invoke-TargetCompose @('up', '-d', '--wait', 'db', 'mailpit')
Invoke-TargetCompose @('run', '--rm', '--no-deps', 'backend', 'bash', 'scripts/prestart.sh')
Invoke-TargetCompose @('up', '-d', '--wait', 'backend')
Write-Host 'Target application: http://127.0.0.1:8001'
Write-Host 'Team C discovery: http://127.0.0.1:8000'
