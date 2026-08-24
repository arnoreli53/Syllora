# Syllora

Assignment and grade tracking app for students.

## Automatic macOS updates

Pushing app changes to `main` runs `.github/workflows/publish-update.yml`. GitHub Actions tests the app, builds an Apple-silicon macOS bundle, verifies its identity and code-signing structure, creates a GitHub release, and publishes the matching `latest.json` manifest.

Installed copies check the manifest attached to the latest GitHub release first, then fall back to `updates/latest.json` for compatibility with existing installations. The downloaded archive is only installed after its SHA-256 checksum, bundle identifier, and code-signing structure have been verified.

To publish an update:

```sh
git add .
git commit -m "Describe the update"
git push origin main
```

Then open the repository's **Actions** tab and wait for **Publish macOS update** to finish. No local release build or manual `latest.json` edit is required.

The workflow currently builds for Apple silicon (`macos-15`). Public distribution still benefits from configuring a Developer ID certificate and Apple notarization; without those, releases use an ad-hoc signature that the updater can verify structurally but macOS Gatekeeper may warn on a fresh installation.
