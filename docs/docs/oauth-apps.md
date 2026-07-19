# OAuth Application Setup

Chief uses OAuth applications to obtain permission to call Google and Dropbox APIs
on behalf of a user. The provider identifies an application with a client ID (called
an app key by Dropbox) and authenticates it with a client secret.

## Callback URLs

A callback URL, also called a redirect URI, is the application endpoint to which an
OAuth provider returns the user's browser after consent. The provider adds temporary
values such as an authorization code and state to the query string; Chief validates
them and exchanges the code for tokens.

The callback URL is not a general landing page. It must match a URL registered with
the provider exactly, including:

- `http` versus `https`;
- hostname and port;
- path capitalization; and
- the trailing slash.

Register every origin from which users connect. For example, local development and
production are separate entries. Use HTTPS outside local development, and never log
callback query strings: they can contain short-lived authorization codes. Production
ingress, access logs, and application monitoring must omit the entire query string
before the request reaches Chief.

Google redirects directly to Chief and therefore needs a registered callback URL.
Dropbox credentials are currently provisioned outside Chief, so Dropbox does not use
a Chief callback URL.

## Google

Chief supports user OAuth grants for Gmail and Google Drive. One Google OAuth
application can serve all Chief users for a deployment.

### Create the application

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and select or
   create a project.
2. Enable the **Gmail API** and/or **Google Drive API**, depending on the Chief tools
   the deployment will use.
3. In **Google Auth Platform**, configure **Branding**, **Audience**, and **Data
   Access**. If the app is external and remains in testing, add each Chief user as a
   test user.
4. Add only the scopes needed by the enabled capabilities:

   | Chief capability | Google scope |
   |------------------|--------------|
   | `gmail_read` | `https://www.googleapis.com/auth/gmail.readonly` |
   | `gmail_modify` | `https://www.googleapis.com/auth/gmail.modify` |
   | `gmail_send` | `https://www.googleapis.com/auth/gmail.send` |
   | `drive_metadata` | `https://www.googleapis.com/auth/drive.metadata.readonly` |

5. Open **Google Auth Platform → Clients**, create an OAuth client, and choose
   **Web application**.
6. Under **Authorized redirect URIs**, add:

   ```text
   https://<chief-origin>/settings/keys/oauth/google/callback/
   ```

   Replace `<chief-origin>` with the exact browser-visible hostname, including a
   non-default port. For local development, use the HTTP origin shown in the browser,
   for example:

   ```text
   http://localhost:8081/settings/keys/oauth/google/callback/
   ```

7. Create the client and copy its client ID and client secret.

An external app left in Google's **Testing** publishing state issues refresh tokens
that expire after seven days for these scopes. Use testing only for development; move
the app to **In production** and complete any verification Google requires before
depending on durable grants. An **Internal** app is appropriate when every user belongs
to the same Google Workspace organization. External production apps may require
Google verification because Gmail permissions include sensitive or restricted scopes.

Google documents the web-server flow and redirect URI requirements in
[Using OAuth 2.0 for Web Server Applications](https://developers.google.com/identity/protocols/oauth2/web-server).

### Configure Chief

For local Docker Compose development, copy `.env.local.example` to `.env.local` and
set the application credentials under `#[backend]`:

```dotenv
GOOGLE_OAUTH_CLIENT_ID=<client-id>
GOOGLE_OAUTH_CLIENT_SECRET=<client-secret>
```

Restart the Chief services after changing these values. In production, store the
same values in the structured Knox secret `$KNOX/chief/oauth/google` using keys
`client_id` and `client_secret`; deployment maps them to the environment variables.

Users can then create a Google OAuth credential on **Settings → Keys**, select the
required capabilities, and choose **Connect**. Chief stores each user's refresh grant
encrypted; it does not copy the OAuth application secret into user credentials.

If Google reports `redirect_uri_mismatch`, compare the URI in the request with the
registered value character by character, especially the scheme, port, and trailing
slash.

## Dropbox

Chief's Dropbox integration uses an app key, app secret, and offline refresh token.
Chief does not currently run the Dropbox consent flow, so an operator provisions the
refresh token separately and then stores all three values as one credential.

### Create the application

1. Open the [Dropbox App Console](https://www.dropbox.com/developers/apps) and choose
   **Create app**.
2. Select **Scoped access**.
3. Choose the access model:
   - **Full Dropbox** for roots in existing account or team content.
   - **App folder** only when every configured Chief root is inside the app's folder.
4. Give the app a unique name and create it.
5. On **Permissions**, enable only `files.metadata.read`, then submit the change.

### Generate an offline refresh token

Open this URL in a browser, replacing `<app-key>`:

```text
https://www.dropbox.com/oauth2/authorize?client_id=<app-key>&response_type=code&token_access_type=offline
```

Approve access and copy the displayed authorization code. Exchange it promptly from
a trusted terminal. The first two commands collect the app key and single-use code;
`curl` then prompts for the app secret without placing it in the command or process
arguments:

```bash
read -r -p "Dropbox app key: " DROPBOX_APP_KEY
read -r -p "Dropbox authorization code: " DROPBOX_AUTHORIZATION_CODE
curl https://api.dropboxapi.com/oauth2/token \
  --user "$DROPBOX_APP_KEY" \
  --data-urlencode "code=$DROPBOX_AUTHORIZATION_CODE" \
  --data "grant_type=authorization_code"
```

The response includes a `refresh_token`. The authorization code is single-use. Keep
the app secret and refresh token out of source control, shell history, logs, and chat.
The [Dropbox OAuth Guide](https://developers.dropbox.com/oauth-guide) describes the
authorization-code and offline-token flow.

### Configure Chief

On **Settings → Keys**, add a `dropbox` credential whose value is:

```json
{
  "app_key": "...",
  "app_secret": "...",
  "refresh_token": "..."
}
```

For a disk-owned credential, put the same JSON in the `value` field of a key file
under `.local/keys/` as a YAML string:

```yaml
name: team-dropbox
type: dropbox
owner: your-username
value: |
  {
    "app_key": "...",
    "app_secret": "...",
    "refresh_token": "..."
  }
```

Chief uses the refresh token to obtain short-lived access tokens. For Dropbox
team-space content, also set the integration's non-secret `config.namespace_id`;
this is separate from OAuth application setup.

See [Chief Agent Documentation](agents.md#credentials) for credential files,
integration references, and cloud-file root configuration.
