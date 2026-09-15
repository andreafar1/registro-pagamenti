# Registro pagamenti

Web app privata per condividere spese e pagamenti tra proprietario e inquilino. Gestisce luce, gas, condominio, riscaldamento e altre spese, con stato pagato/da pagare/scaduto.

## Installazione su Ubuntu

1. Installa Docker e Git:

   ```bash
   sudo apt update
   sudo apt install docker.io docker-compose-v2 git
   sudo systemctl enable --now docker
   ```

2. Clona il repository e crea la configurazione:

   ```bash
   git clone https://github.com/andreafar1/registro-pagamenti.git
   cd registro-pagamenti
   cp .env.example .env
   nano .env
   ```

3. Genera `SECRET_KEY` con `openssl rand -hex 32` e inseriscila in `.env`. Cambia entrambe le password prima dell’avvio.

4. Avvia:

   ```bash
   sudo docker compose up -d --build
   ```

5. Apri `http://localhost:8080` dal PC Ubuntu.

## Aggiornamento

```bash
git pull
sudo docker compose up -d --build
```

## Backup

```bash
chmod +x scripts/backup.sh
sudo ./scripts/backup.sh
```

I backup sono salvati in `backups/` e quelli più vecchi di 30 giorni vengono eliminati. Lo script salva sia il database sia gli allegati presenti in `data/uploads/`. Copia periodicamente i backup anche su un altro dispositivo.

## Accesso da Internet

La porta è esposta soltanto su `127.0.0.1`, quindi non va aperta direttamente sul router. Per l’accesso esterno configura Cloudflare Tunnel verso `http://localhost:8080`. Quando HTTPS è attivo, imposta `COOKIE_SECURE=true` nel file `.env` e riavvia con `sudo docker compose up -d`.

## Nota sulle credenziali

Gli utenti vengono creati al primo avvio. Per cambiare le password dopo che il database è stato inizializzato, elimina il database solo se non contiene dati oppure usa la procedura di ripristino amministrativo. Non pubblicare mai il file `.env`.
