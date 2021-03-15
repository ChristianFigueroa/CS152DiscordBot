import urllib
import urllib.request
import cv2
import os
import numpy as np
from multiprocessing.dummy import Pool as ThreadPool 
import itertools
import re
import socket

socket.setdefaulttimeout(15)

pic_num = 1

def store_raw_images(paths, links):
    global pic_num
    for link, path in zip(links, paths):
        if not os.path.exists(path):
            os.makedirs(path)
        image_urls = urllib.request.urlopen(link).read().decode("utf-8").strip()
        
        pool = ThreadPool(32)
        pool.starmap(loadImage, zip(itertools.repeat(path), (str(url).strip() for url in image_urls.split('\n')), itertools.count(pic_num))) 
        pool.close() 
        pool.join()
        print(f"\nFinished {path}\n")

def loadImage(path, link, counter):
    global pic_num
    if pic_num < counter:
        pic_num = counter+1;
    try:
        filename = f"{path}/{counter}.jpg"
        urllib.request.urlretrieve(link, filename)
        img = cv2.imread(filename)
        if img is not None:
            cv2.imwrite(filename, img)
            print(f"Finished {counter} ({link})")
    except Exception as e:
        print(f"Failed on {counter} ({link}): {e}")
    
def removeInvalid(dirPaths):
    for dirPath in dirPaths:
        for img in os.listdir(dirPath):
            for invalid in os.listdir('invalid'):
                try:
                    current_image_path = str(dirPath)+'/'+str(img)
                    invalid = cv2.imread('invalid/'+str(invalid))
                    question = cv2.imread(current_image_path)
                    if question is None or (invalid.shape == question.shape and not(np.bitwise_xor(invalid,question).any())):
                        os.remove(current_image_path)
                        print(f"Removed {current_image_path}")
                        break
                except Exception as e:
                    print(str(e))
  
def main():
    links = [ 
            'http://image-net.org/api/text/imagenet.synset.geturls?wnid=n01318894', \
            'http://image-net.org/api/text/imagenet.synset.geturls?wnid=n03405725', \
            'http://image-net.org/api/text/imagenet.synset.geturls?wnid=n07942152', \
            'http://image-net.org/api/text/imagenet.synset.geturls?wnid=n00021265', \
            'http://image-net.org/api/text/imagenet.synset.geturls?wnid=n07690019', \
            'http://image-net.org/api/text/imagenet.synset.geturls?wnid=n07865105', \
            'http://image-net.org/api/text/imagenet.synset.geturls?wnid=n07697537' ]
    
    paths = ['pets', 'furniture', 'people', 'food', 'frankfurter', 'chili-dog', 'hotdog']    
    
    store_raw_images(paths, links)
    removeInvalid(paths)


if __name__ == "__main__":
    main()
