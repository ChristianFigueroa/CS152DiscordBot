Use `python3 bot.py` tun run the bot.

Use `python3 content_reviewer.py` to run the image classifier on each image file in `dataset`. Microsoft's ComputerVision API has a 20-photos-per-minute limit, so only the first 20 images in the dataset will be marked. You can specify another set of 20 by specifying an index:

```
python3 content_reviewer.py # Scans images 0-19
python3 content_reviewer.py 20 # Scans images 20-39
```