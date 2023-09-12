# use open cv to create point cloud from depth image.
import utils.setup_path as setup_path 
import airsim

import os
import time
import math
import copy

import torch
import cv2
import numpy as np
import open3d as o3d
import plotly.graph_objects as go

import utils.binvox_rw as binvox_rw

from config.default import args

def savePointCloud(pcd, fileName):
   o3d.io.write_point_cloud(fileName, pcd)

def getRGBImage(client):
   # Request DepthPerspective image as uncompressed float
   responses = client.simGetImages(
      [
         airsim.ImageRequest("0", airsim.ImageType.Scene , False, False)
      ]
   )
   rgb_response = responses[0]

   # get numpy array
   img1d = np.fromstring(rgb_response.image_data_uint8, dtype=np.uint8) 

   # reshape array to 4 channel image array H X W X 3
   rgb_img = img1d.reshape(rgb_response.height, rgb_response.width, 3)

   return rgb_img

def getImages(client):
   # Request DepthPerspective image as uncompressed float
   responses = client.simGetImages(
      [
         airsim.ImageRequest("0", airsim.ImageType.Scene , False, False),
         airsim.ImageRequest("0", airsim.ImageType.DepthPerspective, True, False),
      ]
   )
   rgb_response, depth_response = responses[0], responses[1]

   # get numpy array
   img1d = np.fromstring(rgb_response.image_data_uint8, dtype=np.uint8) 

   # reshape array to 4 channel image array H X W X 3
   rgb_img = img1d.reshape(rgb_response.height, rgb_response.width, 3)

   # Reshape to a 2d array with correct width and height
   depth_img = airsim.list_to_2d_float_array(depth_response.image_data_float, depth_response.width, depth_response.height)
   depth_img = depth_img.reshape(depth_response.height, depth_response.width, 1)

   # Convert depth_img to millimeters to fill out 16bit unsigned int space (0..65535). Also clamp large values (e.g. SkyDome) to 65535
   depth_img = depth_img * 1000
   depth_img = np.clip(depth_img, 0, 65535).astype(np.uint16)

   return rgb_img, depth_img

def getPointCloudByIntrinsic(client):
   rgb_img, depth_img = getImages(client)

   color = o3d.geometry.Image(rgb_img)
   depth = o3d.geometry.Image(depth_img)

   rgbd_image = o3d.geometry.RGBDImage.create_from_color_and_depth(
      color, depth, depth_scale=1000.0, depth_trunc=40.0, convert_rgb_to_intensity=False
   )

   intrinsics = o3d.camera.PinholeCameraIntrinsic(1280, 720, *get_intrinsic())
   point_cloud = o3d.geometry.PointCloud.create_from_rgbd_image(rgbd_image, intrinsics)
   return point_cloud

def get_intrinsic():
   # 하드 코딩한 결과
   # 다른 방법 없음
   w = 224
   h = 224 
   fov = 90

   fx = w / (2 * math.tan(fov / 2))
   fy = h / (2 * math.tan(fov / 2))
   cx = w / 2
   cy = h / 2

   return (fx, fy, cx, cy)

def getPointCloud(client):
   rgb_img, depth_img = getImages(client)
   point_cloud = cv2.reprojectImageTo3D(depth_img, get_proj_matrix(client))
   return point_cloud_to_o3d(point_cloud, rgb_img)

def point_cloud_to_o3d(point_cloud, rgb_img):
   flattened_rgb = rgb_img.reshape(-1, 3)
   pcd = o3d.geometry.PointCloud()
   pcd.points = o3d.utility.Vector3dVector(point_cloud.reshape(-1, 3))
   pcd.colors = o3d.utility.Vector3dVector(flattened_rgb / 255.0)
   return pcd

def get_proj_matrix(client):
   # 드론의 현재 위치 및 자세 정보를 얻음
   camera_info = client.simGetCameraInfo("0")
   proj_matrix = np.array(camera_info.proj_mat.matrix)
   return proj_matrix

def mergePointClouds(cloud1, cloud2, client):
   source = copy.deepcopy(cloud1)
   target = copy.deepcopy(cloud2)

   # coarse-to-fine manner의 Iterative Closest Point(ICP) 알고리즘을 사용하여 두 포인트 클라우드를 정합
   threshold = 0.02
   T = get_proj_matrix(client)
   reg_p2p = o3d.pipelines.registration.registration_icp(
                     source, target, threshold, T, 
                     o3d.pipelines.registration.TransformationEstimationPointToPoint())

   # 변환 행렬을 사용하여 source 포인트 클라우드를 변환
   source.transform(reg_p2p.transformation)

   # 변환된 포인트 클라우드와 target 포인트 클라우드를 결합
   merged_pcd = source + target
   return merged_pcd

def pcd_to_voxel_tensor(pcd):
   # 포인트 클라우드에서 복셀 그리드 생성
   voxel_size = args.voxel_size
   voxel_grid = o3d.geometry.VoxelGrid.create_from_point_cloud(pcd, voxel_size=voxel_size)
   
   # 복셀 그리드의 바운딩 박스 얻기
   min_bound = voxel_grid.get_min_bound() // voxel_size
   max_bound = voxel_grid.get_max_bound() // voxel_size
   dims = np.asarray(max_bound - min_bound, dtype=np.int)

   # 빈 텐서(모든 값이 0) 생성
   tensor = torch.zeros(*dims, dtype=torch.float32)

   # 복셀의 중심 포인트 얻기
   centers = np.asarray(voxel_grid.get_voxels(), dtype=np.float32)[:, :3]  # (num_voxels, 3)
   indices = np.round((centers - min_bound) / voxel_size).astype(np.int)

   # 텐서에 복셀 값 설정
   tensor[indices[:, 0], indices[:, 1], indices[:, 2]] = 1.0
   return tensor

def getMapPointCloud(client, voxel_size):
   curr_dir = "\\".join(os.path.dirname(os.path.abspath(__file__)).split("\\")[:-1])
   binvox_path = os.path.join(curr_dir, "maps", f"map-{voxel_size}.binvox")
   if not os.path.exists(binvox_path):
      print("Create map voxel...")
      center = airsim.Vector3r(0, 0, 0)
      client.simCreateVoxelGrid(center, 1000, 1000, 100, voxel_size, binvox_path)

   # 복셀 데이터 읽기
   with open(binvox_path, 'rb') as f:
      voxel_data = binvox_rw.read_as_3d_array(f)

   filled_voxels = np.where(voxel_data.data)
   coords = np.array(list(zip(*filled_voxels)))
   
   pcd = o3d.geometry.PointCloud()
   pcd.points = o3d.utility.Vector3dVector(coords * voxel_data.scale + voxel_data.translate)

   return pcd