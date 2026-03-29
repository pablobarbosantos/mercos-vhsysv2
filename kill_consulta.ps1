$pid8082 = (Get-NetTCPConnection -LocalPort 8082 -ErrorAction SilentlyContinue).OwningProcess
if ($pid8082) {
    Stop-Process -Id $pid8082 -Force
    Write-Host "Processo $pid8082 encerrado (porta 8082)."
} else {
    Write-Host "Nenhum processo na porta 8082."
}
