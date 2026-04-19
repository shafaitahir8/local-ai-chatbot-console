param(
    [string]$NssmPath = "C:\\nssm\\nssm.exe",
    [string]$ProjectRoot = "D:\\JupyterNotebooks\\Chatbots\\API Management Console"
)

$python = Join-Path $ProjectRoot "venv\\Scripts\\python.exe"
$roles = @("scheduler", "scraper-worker", "embedding-worker", "gateway-agent", "tunnel-agent")

& $NssmPath install chatbot-api $python "-m uvicorn app.main:app --host 0.0.0.0 --port 8000"
& $NssmPath set chatbot-api AppDirectory $ProjectRoot
& $NssmPath set chatbot-api AppEnvironmentExtra "APP_ENV=production" "APP_RUNTIME_ROLE=api"
& $NssmPath set chatbot-api Start SERVICE_AUTO_START

foreach ($role in $roles) {
    $serviceName = "chatbot-$role"
    & $NssmPath install $serviceName $python "-m app.runtime_supervisor $role"
    & $NssmPath set $serviceName AppDirectory $ProjectRoot
    & $NssmPath set $serviceName AppEnvironmentExtra "APP_ENV=production"
    & $NssmPath set $serviceName Start SERVICE_AUTO_START
}
