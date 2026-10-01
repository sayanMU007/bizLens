# Deploy BizLens (one service, one link, ~10 minutes)

Uses Render's free tier. No separate database or frontend host is needed.

1. **Push to GitHub.** Before pushing, confirm `.env` is NOT in the repo (`.gitignore` excludes it;
   check on github.com after pushing).
2. Go to https://render.com and sign in with GitHub.
3. **New +  ->  Blueprint**, pick your repo. Render reads `render.yaml` and proposes one web service
   called `bizlens`.
4. When asked for `GROQ_API_KEY`, paste your key. Click **Apply** / **Deploy**.
5. Wait for the build (3-6 min). Your link is `https://bizlens-xxxx.onrender.com`. That is the link to submit.
6. Open it, click **Use sample sales.csv**, and run the demo question to confirm it works.

## Know before you present
- **Free instances sleep after ~15 min idle** and take ~1 minute to wake. Open the link and run
  one analysis 5 minutes before judges look, and mention it in your submission notes.
- **Data resets on restart** (free tier has no disk). Click *Use sample sales.csv* again.
- The `ask` endpoint is rate limited per IP (default 60/hour; set `ASK_RATE_LIMIT` to change)
  so a public link cannot drain your Groq quota.
- Check the deploy: `https://<your-link>/api/health/` should show `"status":"ok"`.

## If the build fails
Open the service's **Logs** tab on Render and send me the last ~30 lines.
