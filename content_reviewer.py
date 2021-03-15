import sys
import os.path
import json
import requests
from io import BytesIO

from PIL import Image
from imagehash import dhash as difference_hash
import cv2

import numpy as np
from keras.models import load_model

from azure.cognitiveservices.vision.computervision import ComputerVisionClient
from msrest.authentication import CognitiveServicesCredentials

TOKEN_PATH = "tokens.json"

IMG_SIZE = 128
HASH_SIZE = 12
CSAM_SCORE_THRESHOLD = 0.8
MODEL_GRAYSCALE = False

# Squelch TensorFlow debug messages
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

class ContentReviewer():
    def __init__(self):
        if not os.path.isfile(TOKEN_PATH):
            raise FileNotFoundError(f"{TOKEN_PATH} not found!")
        with open(TOKEN_PATH) as file:
            tokens = json.load(file)
            self.discord_token = tokens["discord"]
            self.perspective_key = tokens["perspective"]
            self.azure_key = tokens["azure"]
            self.azure_endpoint = tokens["azure_endpoint"]
        self.computervision_client = ComputerVisionClient(self.azure_endpoint, CognitiveServicesCredentials(self.azure_key))
        # Load model
        self.csam_model = load_model('model.h5')
        self.csam_model.compile(
            loss="binary_crossentropy",
            optimizer="adam",
            metrics=["accuracy"]
        )
        self.hashlists = {
            "csam": open("csam.hashlist", "a+")
        }
        self.hashlists["csam"].seek(0)
        self.hashes = {
            "csam": list(int(line, 16) for line in self.hashlists["csam"])
        }

    def review_text(self, message):
        PERSPECTIVE_URL = 'https://commentanalyzer.googleapis.com/v1alpha1/comments:analyze'

        url = PERSPECTIVE_URL + '?key=' + self.perspective_key
        data_dict = {
            'comment': {
                'text': message.content
            },
            'languages': ['en'],
            'requestedAttributes': {
                'SEVERE_TOXICITY': {},
                'IDENTITY_ATTACK': {},
                'INSULT': {},
                'THREAT': {},
                'TOXICITY': {},
                'SPAM': {},
                'SEXUALLY_EXPLICIT': {},
                'FLIRTATION': {}
            },
            'doNotStore': True
        }
        response = requests.post(url, data=json.dumps(data_dict))
        response_dict = response.json()

        scores = {}
        for attr in response_dict["attributeScores"]:
            scores[attr] = response_dict["attributeScores"][attr]["summaryScore"]["value"]

        return scores

    async def review_images(self, message, as_array=False):
        scores_list = []
        for attachment in message.attachments:
            if not attachment.height:
                # Non-image attachments will have no height and should be skipped
                scores_list.append({"GORE": 0, "ADULT": 0, "RACY": 0, "CSAM": 0})
                continue

            scores = {}

            # Download the image to a stream
            file_stream = BytesIO()
            await attachment.save(file_stream, use_cached=True)

            # Turn it into a numpy array via cv2
            file_stream.seek(0)
            arr_img = cv2.imdecode(np.asarray(bytearray(file_stream.read()), dtype=np.uint8), cv2.IMREAD_COLOR)

            # Get a CSAM score for the image
            scores["CSAM"] = self.csam_score(arr_img)

            # Check if this image is in our list of blacklisted hashes
            scores["CSAM_HASH"] = self.hash_compare(arr_img)

            # Use Azure to detect other components (including gory, sexually explicit, and racy images)
            # First seek the image stream back to 0 to be read again
            file_stream.seek(0)
            # Get all the scores mentioned above
            results = self.computervision_client.analyze_image_in_stream(file_stream, ["adult"])
            # Looks for blood and gore to mark as promoting violence or terrorism
            scores["GORE"] = results.adult.gore_score
            # Looks for sexually explicit photos to mark as sexual content
            scores["ADULT"] = results.adult.adult_score
            # Looks for suggestive photos to mark as sexual content with a lower priority
            scores["RACY"] = results.adult.racy_score

            # Add this set of scores to the list to move on to the next attachment
            scores_list.append(scores)
        return scores_list

    def csam_score(self, img):
        # `img` should be a numpy array from cv2
        img = cv2.cvtColor(cv2.resize(img, (IMG_SIZE, IMG_SIZE)), cv2.COLOR_BGR2GRAY)
        img = np.reshape(img, (1, IMG_SIZE, IMG_SIZE, 1 if MODEL_GRAYSCALE else 3))

        return self.csam_model.predict(img)[0][0]

    def hash_compare(self, img):
        # `img` should a be a numpy array from cv2
        img = cv2.resize(img, (IMG_SIZE, IMG_SIZE))
        # Convert it to a PIL Image
        pil = Image.fromarray(img)
        # Calculate this image's hash
        dhash = int(str(difference_hash(pil, hash_size=HASH_SIZE)), 16)

        # Iterate through hashes for any hash that is a difference of less than 6
        for _hash in self.hashes["csam"]:
            hash_difference = bin(_hash ^ dhash).count("1")
            if hash_difference <= 6:
                # If this is a slightly different image, add its hash to the hashlist so we can detect against it too
                if hash_difference > 0:
                    self.save_hash(img)
                return True
        # Return False if we never found a matching hash
        return False

    def save_hash(self, img):
        # `img` should a be a numpy array from cv2
        img = cv2.resize(img, (IMG_SIZE, IMG_SIZE))
        pil = Image.fromarray(img)

        # Calculate this image's hash
        dhash = difference_hash(pil, hash_size=HASH_SIZE)

        # Add this has to our existing in-memory list
        self.hashes["csam"].append(int(str(dhash), 16))
        # Write this has to the csam.hashlist file for the future
        self.hashlists["csam"].write(str(dhash) + "\n")
        # Write immediately since we usually don't end up properly close()-ing our file...
        self.hashlists["csam"].flush()

if __name__ == "__main__":
    from azure.cognitiveservices.vision.computervision.models._models_py3 import ComputerVisionErrorException as CVError

    reviewer = ContentReviewer()

    offset = sys.argv[1] if len(sys.argv) > 1 else "0"
    try:
        offset = int(offset)
    except ValueError:
        raise ValueError("Supplied argument must be an int.")

    _, _, files = next(os.walk("dataset"))
    files = list(sorted(filter(lambda file: os.path.splitext(file)[1] in (".jpeg", ".jpg", ".png"), files)))[offset:]

    if len(files) > 20:
        print(f"Scanning files {offset} to {offset + 20} alphabetically (one-minute limit on Azure API); to scan another set of files, provide their index as an argument.")
        files = files[:20]

    skipCV = False
    for file in files:
        img = cv2.imread(f"dataset/{file}", 0 if MODEL_GRAYSCALE else 1)
        img = cv2.resize(img, (IMG_SIZE, IMG_SIZE))
        img = np.reshape(img, (1, IMG_SIZE, IMG_SIZE, 1 if MODEL_GRAYSCALE else 3))

        csam_prediction = reviewer.csam_model.predict(img)[0][0]

        try:
            if not skipCV:
                with open(f"dataset/{file}", "rb") as f:
                    azure_results = reviewer.computervision_client.analyze_image_in_stream(f, ["adult"])
        except CVError:
            skipCV = True

        if skipCV:
            print(f"{file}:\n  {'CSAM':^6}  {'GORE':^6}  {'ADULT':^6}  {'RACY':^6}\n  \u001b[{32 if csam_prediction > CSAM_SCORE_THRESHOLD else 2}m{csam_prediction * 100:6.2f}\u001b[0m  \u001b[33m{'WAIT':^6}\u001b[0m  \u001b[33m{'WAIT':^6}\u001b[0m  \u001b[33m{'WAIT':^6}\u001b[0m")
        else:
            print(f"{file}:\n  {'CSAM':^6}  {'GORE':^6}  {'ADULT':^6}  {'RACY':^6}\n  \u001b[{32 if csam_prediction > CSAM_SCORE_THRESHOLD else 2}m{csam_prediction * 100:6.2f}\u001b[0m  \u001b[{32 if azure_results.adult.gore_score > 0.75 else 2}m{azure_results.adult.gore_score * 100:6.2f}\u001b[0m  \u001b[{32 if azure_results.adult.adult_score > 0.85 else 2}m{azure_results.adult.adult_score * 100:6.2f}\u001b[0m  \u001b[{32 if azure_results.adult.racy_score > 0.8 else 2}m{azure_results.adult.racy_score * 100:6.2f}\u001b[0m")