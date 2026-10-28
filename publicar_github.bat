@echo off
chcp 65001 >nul
title Publicar Moorebot Scout no GitHub (Tiagofcustodio/Scout-homeassistant)
echo ============================================================================
echo   A ENVIAR PROJETO PARA: https://github.com/Tiagofcustodio/Scout-homeassistant
echo ============================================================================
echo.
git branch -M main
git remote set-url origin https://github.com/Tiagofcustodio/Scout-homeassistant-.git
git push -u origin main
echo.
echo ============================================================================
echo   Concluido! Ja pode adicionar o repositorio no Home Assistant!
echo ============================================================================
pause
