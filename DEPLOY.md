# Deploying Over `stickers2.jamesburgat.com/tool`

This app is now designed to run behind the existing `/tool` URL.

## App assumptions

- Flask app served by gunicorn on `127.0.0.1:8000`
- URL base path set to `/tool`
- thermal-transfer printer queue configured in `.env`

## Required `.env`

```env
ADMIN_PASSWORD=...
SECRET_KEY=...
BASE_PATH=/tool
PUBLIC_BASE_URL=https://stickers2.jamesburgat.com
PRINTER_TRANSFER_RASTER=Zebra_Transfer_300
TRANSFER_DPI=300
FIGMA_TOKEN=...
```

If you want public log URLs to include the full path, keep `PUBLIC_BASE_URL` as the origin only. The app will append `/tool/...` automatically.

## Server layout

Suggested install directory:

```bash
/opt/stickers2
```

Systemd unit template:

```bash
ops/stickerapp.service
```

GitHub Actions workflow:

```bash
.github/workflows/deploy.yml
```

## Reverse proxy

Your live URL already uses `/tool`, so your web server should proxy that prefix to gunicorn without stripping it:

```nginx
location /tool/ {
    proxy_pass http://127.0.0.1:8000/tool/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location = /tool {
    return 301 /tool/;
}
```

## Replace flow

1. Copy this repo to `/opt/stickers2`.
2. Create `/opt/stickers2/.env` with the values above.
3. Install the service file as `stickers2.service`.
4. Update nginx so `/tool` points to this app instead of the current receipt-printer service.
5. Restart nginx and `stickers2.service`.
6. Verify:

```bash
curl -fsS http://127.0.0.1:8000/tool/health
curl -I https://stickers2.jamesburgat.com/tool/
```

## Important

The current live `/tool` is not your old `stickers` app. It appears to be a different receipt-printer app, so you likely need to disable or replace that service and its nginx location rather than touching `/opt/sticker-app`.
