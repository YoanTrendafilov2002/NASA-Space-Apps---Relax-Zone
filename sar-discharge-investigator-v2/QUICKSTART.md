# Run SARtainty on another computer

Extract the ZIP, open the `SARtainty` folder, and run the launcher with Python 3.11 or newer. It creates an app-local virtual environment, installs the Python dependencies on first run, and starts the explorer. The app includes calibrated NISAR downloads, map previews, and the fitted question router. The standalone assistant works on a computer without Ollama.

**Windows PowerShell**

```powershell
py -3 launch_relax_zone.py --without-ollama
```

**macOS or Linux**

```sh
python3 launch_relax_zone.py --without-ollama
```

Open [SARtainty on this computer](http://127.0.0.1:4174/). Try **Show me wetlands**, then **What can I compare at Curuai?**. Press Ctrl+C in the server terminal to stop it. Use `--port 4175` if port 4174 is in use.

Setup fetches Python packages. After setup, the map, prepared imagery, analysis and standalone assistant work locally without an internet connection. Live catalogue search, external source links and new science downloads use the provider network. The Earthdata handoff opens in the user's signed-in browser.

## Run an analysis

Select a study area, open **Acquisitions**, and use **Analyze selection**. Choose the dates and radar channel. At Qeshm, compare 17 July and 22 August 2026 in HH to generate a new change layer. Run a time series with all three Qeshm HH dates to chart the radar return over the same measured cells. Download the generated GeoTIFF during the session to keep the result.

Use **Compare previews** when you want a quick look around one date. Select 10 August 2026 in Qeshm and keep the default 24 days to see captures on either side. Drag the slider to any day. Between passes, it shows the nearest available capture and gives the actual image date. The first image anchors the comparison. SARtainty summarizes overall appearance for matching previews. Brightness can also change because each provider preview is rendered for display. Use **Analyze selection** for measured radar change. [NISAR GCOV browse settings](https://github.com/isce-framework/isce3/blob/develop/share/nisar/defaults/gcov.yaml#L550-L619)

The wetland screen uses prepared HH and HV at Curuai, Hasik, and the UAE coastal review. Cyan outlines show open-water candidates, and dotted tan outlines show uncertain water edges. The UAE coast is withheld by the noise gate. The separate flooded-vegetation class requires a comparable drier acquisition and a registered vegetation reference. At Shark River Slough, the saved review shows modeled water depth in mapped marsh and agreement with the earlier radar benchmark. The oil screen runs on prepared HH observations at Eugene Island and Eloi Bay.

Ask the assistant **How does the NISAR Ecosystems ATBD distinguish open water and flooded vegetation?** Replies cite the relevant pages of the official design document. The source summaries work offline. When available, local Qwen selects a relevant summary from the retrieved choices.

Open **Use-case settings** to compare recommended products, channels, date choices and quality checks. The four guidance profiles for earthquake displacement, short-term and long-term ground deformation, and sea ice motion link to the Solid Earth and Sea Ice Motion ATBDs. They provide reference settings for workflows outside the app's current GCOV analyses. Choose **Ask the assistant** to discuss a selected profile, including follow-ups such as **How far apart should the acquisitions be?** The settings drawer keeps the map selection in place.

To add dates for change and time series on a self-hosted copy, append a trusted `data/nisar-imported.json` catalogue using the same `sites`, `observations` and `evidence` structure as `data/nisar-holdouts.json`. Give new rows distinct IDs and provide their checksum-registered NISAR L-SAR GCOV gamma-zero GeoTIFF crops in `data/science/` and their map previews in `data/sar/`. Restart the explorer. The analysis checks the selected acquisitions' geometry, processing version and exact native pixel grid. Provider browse previews alone cannot supply those measurements.

Python clients can send the same selection to `POST /api/explorer/analyze` with a JSON content type.

```json
{
  "kind": "change",
  "site_id": "qeshm-nisar",
  "polarization": "HH",
  "observation_ids": ["nisar-qeshm-20260717", "nisar-qeshm-20260822"]
}
```

Use `time_series` for two to six dates or `wetland` and `oil` for a single prepared HH observation. Completed runs include a run ID, input provenance and results. Download links for generated assets are served from a temporary cache.

## Optional local Qwen

Install and start Ollama on the host. In a terminal, pull the supported model if it is missing, then launch the app without `--without-ollama` so it can detect Ollama at `127.0.0.1:11434`. [Ollama downloads](https://ollama.com/download) · [Qwen3.5 model](https://ollama.com/library/qwen3.5:4b)

```powershell
ollama pull qwen3.5:4b
py -3 launch_relax_zone.py
```

On macOS or Linux, replace `py -3` with `python3`. The fitted matrix router and measured answers run locally in either mode.

## Optional public link

Install `cloudflared` on the host. Start a temporary tunnel in one terminal and keep that terminal open. [Cloudflare downloads](https://developers.cloudflare.com/tunnel/downloads/) · [Quick Tunnel documentation](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)

```sh
cloudflared tunnel --url http://127.0.0.1:4174 --http-host-header 127.0.0.1:4174
```

In a second terminal, start Relax Zone with `--public-origin` set to the exact HTTPS origin printed by Cloudflare. Use the scheme and hostname only. If a local Relax Zone server is already using that port, stop it first. Replace the example hostname below.

```powershell
py -3 launch_relax_zone.py --without-ollama --public-origin "https://your-generated-host.trycloudflare.com"
```

Open or share the generated URL. Keep the explorer and tunnel running. A new tunnel may generate a different hostname. Visitors use their own Earthdata session to open protected provider links.

## Verify or retrain

The package manifest lists the SHA-256 and byte size of each application file. The trained router's evaluation and confusion matrices are in `data/matrix-assistant-model.json`. To reproduce the router with its curated training questions, run `py -3 models/train_matrix_assistant.py` from the extracted folder. This uses the Python standard library.

For app regression checks, run `py -3 launch_relax_zone.py --setup-only` and then run the tests with the app environment.

```powershell
& .\.relax-zone-venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

On macOS or Linux, use `.relax-zone-venv/bin/python` in place of the Windows path. See [NISAR data review](NISAR_DATA_REVIEW.md) for layer provenance and interpretation.
