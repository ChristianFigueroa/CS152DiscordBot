import sys
import glob
import cv2
from skimage import exposure
from sklearn.model_selection import train_test_split
import numpy as np
from keras.models import Sequential
from keras.utils.np_utils import to_categorical
from keras.layers.convolutional import Conv2D, Cropping2D
from keras.layers import Dense, Dropout, Activation, Flatten, ELU

GRAYSCALE = False

def rotateImage(img, angle):
    if GRAYSCALE:
        rows, cols = img.shape
    else:
        rows, cols, ch = img.shape
    M = cv2.getRotationMatrix2D((cols/2,rows/2), angle, 1)
    return cv2.warpAffine(img, M, (cols,rows))

def loadBlurImg(path, imgSize):
    img = cv2.imread(path, 0 if GRAYSCALE else 1)
    # img = rotateImage(img, np.random.randint(-30, 30))
    img = cv2.blur(img, (2,2))
    img = cv2.resize(img, imgSize)
    return img

def loadImgClass(classPath, classLabel, classSize, imgSize):
    x = []
    y = []

    # Undersampling
    if len(classPath) > classSize:
        classPath = np.random.choice(classPath, size=classSize, replace=False)
    # Oversampling
    elif len(classPath) < classSize:
        classPath = classPath + list(np.random.choice(classPath, size=classSize - len(classPath), replace=True))
    
    for path in classPath:
        img = loadBlurImg(path, imgSize)
        x.append(img)
        y.append(classLabel)

    return x, y

def loadData(img_size, classSize):
    hotdogs = glob.glob('./data/hotdog/**/*.jpg', recursive=True)
    notHotdogs = glob.glob('./data/not-hotdog/**/*.jpg', recursive=True)
    
    imgSize = (img_size, img_size)
    print(f"Loading {classSize} hotdogs from {len(hotdogs)} images")
    xHotdog, yHotdog = loadImgClass(hotdogs, 0, classSize, imgSize)
    print(f"Loading {classSize} not-hotdogs from {len(notHotdogs)} images")
    xNotHotdog, yNotHotdog = loadImgClass(notHotdogs, 1, classSize, imgSize)
    print(f"There are now {len(xHotdog)} hotdogs")
    print(f"There are now {len(xNotHotdog)} not-hotdogs")

    X = np.array(xHotdog + xNotHotdog)
    y = np.array(yHotdog + yNotHotdog)
    
    return X, y

def kerasModel(inputShape):
    model = Sequential()
    model.add(Conv2D(16, kernel_size=8, strides=4, padding="valid", input_shape=inputShape))
    model.add(ELU())
    model.add(Conv2D(32, kernel_size=5, strides=2, padding="same"))
    model.add(ELU())
    model.add(Conv2D(64, kernel_size=5, strides=2, padding="same"))
    model.add(Flatten())
    model.add(Dropout(.2))
    model.add(ELU())
    model.add(Dense(512))
    model.add(Dropout(.5))
    model.add(ELU())
    model.add(Dense(2))
    model.add(Activation("sigmoid"))
    return model

def main():
    if len(sys.argv) > 1:
        MODEL_NAME = sys.argv[1]
        if MODEL_NAME[-3:] != ".h5":
            MODEL_NAME += ".h5"
    else:
        from time import strftime
        MODEL_NAME = strftime("model-%m.%d.%y-%H.%M.%S.h5")

    # Size to resize images to
    IMG_SIZE = 128
    # The number of images to generate for each class
    CLASS_SIZE = 3000

    scaled_X, y = loadData(IMG_SIZE, CLASS_SIZE)

    n_classes = len(np.unique(y))

    y = to_categorical(y)

    rand_state = np.random.randint(0, 100)

    print("Splitting data")
    X_train, X_test, y_train, y_test = train_test_split(scaled_X, y, test_size=0.2, random_state=rand_state)

    if GRAYSCALE:
        inputShape = (IMG_SIZE, IMG_SIZE, 1)
        X_train = np.expand_dims(X_train, 3)
        X_test = np.expand_dims(X_test, 3)
    else:
        inputShape = (IMG_SIZE, IMG_SIZE, 3)

    print("Number of classes =", n_classes)
    print("train shape X", X_train.shape)
    print("train shape y", y_train.shape)

    model = kerasModel(inputShape)

    model.compile("adam", "binary_crossentropy", ["accuracy"])
    history = model.fit(X_train, y_train, epochs=10, validation_split=0.1)

    metrics = model.evaluate(X_test, y_test)
    for metric_i in range(len(model.metrics_names)):
        metric_name = model.metrics_names[metric_i]
        metric_value = metrics[metric_i]
        print('{}: {}'.format(metric_name, metric_value))

    model.save(MODEL_NAME)

    print(f"Saved model to {MODEL_NAME}")

if __name__ == "__main__":
    main()