# Deploying To The Pi Live Slot

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

## Actual Pi target

Current live app directory:

```bash
/opt/receipt-printer
```

Current live service:

```bash
receipt-printer.service
```

GitHub Actions workflow:

```bash
.github/workflows/deploy.yml
```

## Replace flow

The easiest deployment path is to overwrite the existing receipt-printer app in place.

1. Keep the existing `receipt-printer.service`.
2. Keep the existing host/proxy setup.
3. Have GitHub Actions sync this repo into `/opt/receipt-printer`.
4. Restart `receipt-printer.service`.
5. Verify:

```bash
curl -fsS http://127.0.0.1:8000/tool/health
curl -I https://stickers2.jamesburgat.com/tool/
```

## One-time Pi prep

Edit `/opt/receipt-printer/.env` so it contains:

```env
BASE_PATH=/tool
PUBLIC_BASE_URL=https://stickers2.jamesburgat.com
ADMIN_PASSWORD=...
SECRET_KEY=...
PRINTER_TRANSFER_RASTER=Zebra_Transfer_300
TRANSFER_DPI=300
FIGMA_TOKEN=...
```

Existing extra receipt-printer env vars can stay; this app will ignore what it does not use.

## Important

The workflow now targets the actual live slot on the Pi:

- app dir: `/opt/receipt-printer`
- service: `receipt-printer.service`
- live route: `/tool`
