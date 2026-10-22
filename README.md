# Demo Clinic WhatsApp Booking Bot

A small appointment-booking demo for an aesthetics clinic. The app uses FastAPI,
SQLite, the Anthropic tool-use API, Twilio's WhatsApp Sandbox, and a named
Cloudflare Tunnel. It is intended for a sales demo, not for storing real patient
or medical information.

## 1. Install and configure

Requirements: Python 3.12, a Twilio account with the WhatsApp Sandbox enabled,
an Anthropic API key and a domain managed by
Cloudflare.

```bash
git clone https://github.com/diegoezce/wapbot.git
cd wapbot
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Set these values in `.env`:

```dotenv
ANTHROPIC_API_KEY=your-anthropic-api-key
ANTHROPIC_MODEL=claude-haiku-4-5
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_WHATSAPP_NUMBER=whatsapp:+17372508034
PUBLIC_BASE_URL=https://appointments.your-domain.com
```

Use the WhatsApp Sandbox sender shown in your Twilio Console if it differs from
the example. Do not commit `.env`. The SQLite database is created as
`demo_clinic.db` in the project directory and seeded at startup. Override its
location with `DATABASE_PATH` if desired.

## 2. Verify the agent in the terminal

Start a local chat first; this uses the same agent and database as WhatsApp, but
does not require Twilio settings:

```bash
python scripts/chat.py
```

Try asking for services, a date's availability, the address, or to book a
service. The agent must ask you to confirm the exact appointment details before
it writes a booking. Type `/quit` to exit.

Run the automated tests:

```bash
python -m pytest -q
```

## 3. Run the FastAPI app locally

With all `.env` values set:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Check the health endpoint from another terminal:

```bash
curl http://localhost:8000/health
```

It should return `{"status":"ok"}`. The webhook endpoint is
`POST http://localhost:8000/webhook`.

## 4. Create a named Cloudflare Tunnel

Install `cloudflared` on the mini PC that will run the app. For macOS with
Homebrew:

```bash
brew install cloudflared
```

Sign in and create a persistent, named tunnel (replace the tunnel name as you
wish):

```bash
cloudflared tunnel login
cloudflared tunnel create demo-clinic
```

The create command prints a tunnel UUID and writes its credentials JSON under
`~/.cloudflared/`. Route a hostname in a DNS zone managed by Cloudflare to that
named tunnel. Replace the example hostname with a domain you own:

```bash
cloudflared tunnel route dns demo-clinic appointments.your-domain.com
```

Create `~/.cloudflared/config.yml`, substituting the UUID and the full path to
the credentials file printed/created above:

```yaml
tunnel: YOUR-TUNNEL-UUID
credentials-file: /Users/YOUR-USER/.cloudflared/YOUR-TUNNEL-UUID.json

ingress:
  - hostname: appointments.your-domain.com
    service: http://localhost:8000
  - service: http_status:404
```

Keep the Uvicorn app running on port 8000. Verify the tunnel interactively
before installing its service:

```bash
cloudflared tunnel run demo-clinic
```

Visit `https://appointments.your-domain.com/health`; it should return
`{"status":"ok"}`. Stop the foreground tunnel with Ctrl-C. Install the
Cloudflare service so it starts when the mini PC starts:

```bash
sudo cloudflared service install
```

On macOS, `cloudflared service install` installs the launchd service. On Linux
it installs the system service for the host's service manager. The service
reads the default `~/.cloudflared/config.yml`; ensure the account running it can
read the credentials file. Check service status using the operating system's
service manager after installation.

The public URL must match the hostname exactly:

```dotenv
PUBLIC_BASE_URL=https://appointments.your-domain.com
```

Restart Uvicorn after changing `.env`. Twilio's signature check reconstructs
the public URL from this setting because the app itself sees the tunnel's local
connection.

## 5. Configure Twilio WhatsApp Sandbox

1. In the Twilio Console, open **Messaging → Try it out → Send a WhatsApp
   message** and activate the WhatsApp Sandbox.
2. Under the Sandbox settings, set **WHEN A MESSAGE COMES IN** to
   `https://appointments.your-domain.com/webhook` with method **HTTP POST**.
3. Save the setting and make sure the app and named tunnel are running.
4. On your phone, open WhatsApp and send the Sandbox's displayed join phrase
   (for example, `join abc-def`) to the Sandbox number shown in the Console.
   The join phrase and Sandbox number are specific to your Twilio account.
5. Send a message such as `Hi, what services do you offer?` to test the bot.

The webhook validates `X-Twilio-Signature` against the public URL, returns
Twilio an immediate empty response, and processes the conversation in a
background task before sending the reply through Twilio's REST API.

## 6. Sample client demo

**Customer:** Hi! What services do you offer?  
**Demo Clinic:** We offer a Skin Consultation (30 min, $50.00), Signature
Facial (60 min, $120.00), and Chemical Peel (45 min, $150.00). Which one are
you interested in?

**Customer:** A Signature Facial. Are there any openings Monday?  
**Demo Clinic:** I can check that. What date do you mean by Monday?

**Customer:** January 4, 2027.  
**Demo Clinic:** I have a 9:00 AM opening with Ava. Would you like to book it?

**Customer:** Yes, please. My name is Taylor.  
**Demo Clinic:** Please confirm: Signature Facial with Ava on Monday,
January 4 at 9:00 AM for $120.00.

**Customer:** Yes.  
**Demo Clinic:** You're booked for a Signature Facial with Ava on Monday,
January 4 at 9:00 AM. See you then!

Availability and booking depend on the seeded schedule and current bookings;
use the terminal chat to choose an upcoming date when giving a live demo.
