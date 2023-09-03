# use open cv to create point cloud from depth image.
import setup_path 
import airsim

import os
import cv2
import time
import sys
import math
import numpy as np

############################################
########## This is work in progress! #######
############################################

# file will be saved in PythonClient folder (i.e. same folder as script)
# point cloud ASCII format, use viewers like CloudCompare http://www.danielgm.net/cc/ or see http://www.geonext.nl/wp-content/uploads/2014/05/Point-Cloud-Viewers.pdf

projectionMatrix = np.array([[-0.501202762, 0.000000000, 0.000000000, 0.000000000],
                              [0.000000000, -0.501202762, 0.000000000, 0.000000000],
                              [0.000000000, 0.000000000, 10.00000000, 100.00000000],
                              [0.000000000, 0.000000000, -10.0000000, 0.000000000]])

def printUsage():
   print("Usage: python point_cloud.py [cloud.txt]")
   
def savePointCloud(image, fileName, color):
   f = open(fileName, "w")
   for x in range(image.shape[0]):
     for y in range(image.shape[1]):
        pt = image[x,y]
        if (math.isinf(pt[0]) or math.isnan(pt[0])):
          # skip it
          None
        else: 
          f.write("%f %f %f %s\n" % (pt[0], pt[1], pt[2]-1, "%d %d %d" % color))
   f.close()

def getPointCloud(client, savePC=False, saveDir="./", color=(0, 0, 0)):
    rawImage = client.simGetImage("0", airsim.ImageType.DepthPerspective)
    assert rawImage is not None, "Camera is not returning image, please check airsim for error messages"

    png = cv2.imdecode(np.frombuffer(rawImage, np.uint8) , cv2.IMREAD_UNCHANGED)
    gray = cv2.cvtColor(png, cv2.COLOR_BGR2GRAY)
    Image3D = cv2.reprojectImageTo3D(gray, projectionMatrix)

    if savePC: 
       save_path = os.path.join(saveDir, outputFile)
       outputFile = f"cloud-{time.time()}.asc" 
       savePointCloud(Image3D, save_path, color)

    return Image3D