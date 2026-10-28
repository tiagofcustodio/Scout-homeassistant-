# 🤖 Moorebot Scout AI & Home Assistant Hub (`Tiagofcustodio/Scout-homeassistant`)

Plataforma completa de **Inteligência Artificial Local (Ollama `llama3.2:3b`)**, **Memória Biométrica (Reconhecimento de Caras e Vozes)**, **Mapeamento Radar 2D & Patrulha Autónoma**, **Controlo Xbox** e **Integração Nativa com Home Assistant** para o robô **Moorebot Scout** (*Roller Eye*).

---

## 🏠 Como Instalar no Home Assistant

Este repositório (`https://github.com/Tiagofcustodio/Scout-homeassistant-`) suporta **ambos os métodos oficiais** do Home Assistant:

### Opção 1: Instalar na Loja de Suplementos (Add-on Store) — Painel na Barra Lateral
Adiciona o painel **Moorebot Scout** diretamente à barra lateral do Home Assistant:
1. No Home Assistant, vá a **Definições** -> **Suplementos (Add-ons)** -> **Loja de Suplementos**.
2. Clique nos **3 pontos (`⋮`)** no canto superior direito -> **Repositórios**.
3. Cole o URL deste repositório e clique em **Adicionar**:
   ```text
   https://github.com/Tiagofcustodio/Scout-homeassistant-
   ```
4. Atualize a loja, clique em **Moorebot Scout AI & Smart Home** -> **Instalar**.
5. No separador **Configuração** do suplemento, coloque o IP do seu Mini PC (ex.: `http://192.168.1.100:8085`), ative **Mostrar na barra lateral** e clique em **Iniciar**!

---

### Opção 2: Instalar via HACS (Integração Nativa com Entidades)
Cria automaticamente todas as entidades nativas no Home Assistant (`camera`, `sensor`, `switch`, `button`, `select`) e serviços (`moorebot_scout.speak`, `moorebot_scout.ask_ai`, `moorebot_scout.stunt`):
1. Abra o **HACS** no Home Assistant -> clique nos **3 pontos (`⋮`)** no canto superior direito -> **Repositórios personalizados (Custom repositories)**.
2. Em **Repositório**, cole:
   ```text
   https://github.com/Tiagofcustodio/Scout-homeassistant-
   ```
3. Em **Categoria (Type)**, escolha **Integração (Integration)** e clique em **Adicionar**.
4. Procure por **Moorebot Scout AI**, clique em **Transferir (Download)** e **reinicie o Home Assistant**.
5. Vá a **Definições** -> **Dispositivos e Serviços** -> **+ Adicionar Integração** -> procure por **Moorebot Scout AI** e introduza o URL do seu Mini PC (ex.: `http://192.168.1.100:8085`).

---

## 🖥️ Correr o Servidor IA + Ollama no Mini PC (Windows)

1. Instale o [Ollama](https://ollama.com) no Mini PC e descarregue o modelo leve:
   ```powershell
   ollama pull llama3.2:3b
   ```
2. Faça duplo clique em **`iniciar_scout_minipc.bat`** (ou inicie com Uvicorn na porta `8085`).
3. Interfaces disponíveis no Mini PC:
   - **Dashboard Completo Cyber-Glass:** `http://<IP-DO-MINIPC>:8085`
   - **Painel Compacto Home Assistant:** `http://<IP-DO-MINIPC>:8085/ha`
