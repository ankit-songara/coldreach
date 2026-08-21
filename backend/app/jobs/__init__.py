"""Background jobs run OUTSIDE the request path (e.g. GitHub Actions cron).

Serverless request handlers die at the 60s Vercel wall, so anything that needs
to run long, uncapped, and unattended lives here and is triggered by an external
scheduler instead. See app/jobs/discover_companies.py and
.github/workflows/daily-discovery.yml.
"""
