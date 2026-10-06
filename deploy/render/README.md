# TrainMeet Server on Render (browser clients)

Create one Docker Web Service in Frankfurt (EU Central), using `Dockerfile.render` and a persistent disk mounted at `/var/lib/trainmeet-server`. Start with Starter compute and a 1 GB disk. Set the health check path to `/healthz`.

Before the first deploy, enter these environment variables directly in Render:

- `TRAINMEET_BOOTSTRAP_ADMIN_NAME`: your display name
- `TRAINMEET_BOOTSTRAP_ADMIN_EMAIL`: your login email
- `TRAINMEET_BOOTSTRAP_ADMIN_PASSWORD`: your password (8–256 characters)

The first owner is created before HTTP opens. Later deploys preserve the existing owner and never reset its password. Remove the three bootstrap variables after verifying the first login. Runtime data and accounts are stored on the persistent disk.

The process reads Render's `PORT` and `RENDER_EXTERNAL_URL` automatically. Set `TRAINMEET_PUBLIC_CLIENT_ORIGIN` explicitly if you use a custom HTTPS domain. Admin login is required behind Render's proxy. MQTT listens only on loopback for the server's internal runtime; physical TMBox clients cannot connect to this broker from outside the container.

Region selection places this service and disk in Frankfurt. TrainMeet Cloud requests and Render's control plane are separate from this service's hosting region.
