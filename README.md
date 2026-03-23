# 🎮 Vinted Pokemon Deal Alert Bot

Monitor pasivo que detecta juegos de Pokémon a precio de ganga en Vinted y envía notificaciones por Telegram.

## Características

- Búsqueda automática de múltiples términos relacionados con Pokémon
- Comparación de precios contra referencias configurables (`prices.json`)
- Alertas Telegram con foto, precio, descuento y enlace directo
- Base de datos SQLite para evitar alertas duplicadas
- Comportamiento anti-detección ético (delays, rotación de User-Agents, jitter)
- Logging rotativo a archivo
- Modo `--dry-run` para probar sin enviar notificaciones
- Lista de exclusión de vendedores

## Requisitos

- Python 3.11+
- Una cuenta de Telegram y un bot creado con [@BotFather](https://t.me/BotFather)

## Instalación

```bash
# Clonar / descargar el proyecto
cd vinted-pokemon-alert

# Crear entorno virtual
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

# Instalar dependencias
pip install -r requirements.txt
```

## Configuración

### 1. Crear bot de Telegram

1. Abre Telegram y habla con [@BotFather](https://t.me/BotFather)
2. Envía `/newbot` y sigue los pasos
3. Copia el **token** que te da BotFather

### 2. Obtener tu Chat ID

1. Habla con [@userinfobot](https://t.me/userinfobot) en Telegram
2. Te responderá con tu **ID numérico**

### 3. Configurar variables de entorno

```bash
cp .env.example .env
# Edita .env con tus datos
```

Variables obligatorias:
| Variable | Descripción |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token de tu bot de Telegram |
| `TELEGRAM_CHAT_ID` | Tu chat ID (o ID de grupo/canal) |

Variables opcionales (con valores por defecto):
| Variable | Default | Descripción |
|---|---|---|
| `POLL_INTERVAL_MIN` | `8` | Mínimo minutos entre ciclos |
| `POLL_INTERVAL_MAX` | `15` | Máximo minutos entre ciclos |
| `REQUEST_DELAY_MIN` | `8.0` | Mínimo segundos entre requests |
| `REQUEST_DELAY_MAX` | `20.0` | Máximo segundos entre requests |
| `DISCOUNT_THRESHOLD` | `0.75` | Ratio para considerar ganga (0.75 = 25% más barato) |
| `VINTED_DOMAIN` | `www.vinted.es` | Dominio de Vinted según país |
| `EXCLUDED_SELLERS` | `` | Vendedores a ignorar (separados por comas) |
| `DB_PATH` | `vinted_pokemon.db` | Ruta de la base de datos |
| `LOG_FILE` | `bot.log` | Ruta del archivo de log |

### 4. Ajustar precios de referencia

Edita `prices.json` para ajustar los precios según el mercado actual:

```json
{
  "pokemon platino": 65,
  "pokemon alma de plata": 70,
  ...
}
```

El bot detectará una ganga cuando: `precio_listing < precio_referencia × DISCOUNT_THRESHOLD`

Con el threshold por defecto (0.75), un Pokemon Platino a menos de **48.75€** dispara una alerta.

## Uso

### Modo normal

```bash
python main.py
```

### Modo dry-run (sin notificaciones)

```bash
python main.py --dry-run
```

Útil para probar la configuración y ver qué listings detecta sin enviar mensajes.

## Formato de notificación

```
🎮 GANGA DETECTADA

📦 Pokemon Platino (DS) - Buen estado
💰 28€ (precio ref: 65€) → -57%
👤 Vendedor: usuario123
🔗 Ver en Vinted
⏰ Hace 3 minutos
```

## Estructura del proyecto

```
vinted-pokemon-alert/
├── main.py          # Loop principal y orquestación
├── scraper.py       # Cliente HTTP para la API de Vinted
├── analyzer.py      # Detección de gangas vs. precios de referencia
├── notifier.py      # Envío de alertas por Telegram
├── database.py      # Persistencia SQLite
├── config.py        # Configuración desde .env
├── prices.json      # Precios de referencia por juego
├── .env.example     # Plantilla de variables de entorno
└── requirements.txt
```

## Consideraciones éticas y legales

- El bot **solo monitoriza** y notifica. No realiza compras automáticas.
- Respeta los tiempos de espera entre requests para no sobrecargar Vinted.
- Consulta los [Términos de Servicio de Vinted](https://www.vinted.es/terms-and-conditions) antes de usar.
- Los delays configurados por defecto (8-20s entre requests, 8-15 min entre ciclos) son conservadores y respetuosos.

## Logs

El bot genera logs en consola y en `bot.log` (rotación automática cada 5 MB, 3 backups):

```
2024-03-20 10:30:00 [INFO] main: Bot started. Dry-run: False | Threshold: 75% | Interval: 8-15 min
2024-03-20 10:30:05 [INFO] scraper: Search 'pokemon': 96 items found, 87 parsed
2024-03-20 10:30:14 [INFO] analyzer: DEAL: 'Pokemon Platino DS' at 25.00€ (ref 65.00€, -61.5%)
2024-03-20 10:30:15 [INFO] main: Cycle complete. Deals sent: 1 | DB stats: {'total_seen': 134, 'total_deals': 3}
```

## Solución de problemas

**El bot no envía notificaciones:**
- Verifica que `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` estén bien configurados
- Asegúrate de haber iniciado una conversación con tu bot en Telegram
- Usa `--dry-run` para comprobar que el scraping funciona

**No se detectan gangas:**
- Comprueba `prices.json` — quizás los precios de referencia son muy altos/bajos
- Reduce `DISCOUNT_THRESHOLD` (ej: `0.85` para alertas con solo 15% de descuento)
- Usa `--dry-run` y revisa los logs para ver qué listings se están procesando

**Errores 401/429 de Vinted:**
- Vinted protege su API; los errores 401 son normales al iniciar sesión
- El bot reintenta automáticamente con backoff exponencial
- Si persisten, aumenta los delays en `.env`
