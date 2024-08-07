import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision import models, transforms
import os
from skimage.transform import resize
from tqdm import tqdm
from matplotlib import pyplot as plt
from ipywidgets import interact, IntSlider
import shutil

class RISE(nn.Module):
    def __init__(self, model, input_size, gpu_batch=100):
        super(RISE, self).__init__()
        self.model = model
        self.input_size = input_size
        self.gpu_batch = gpu_batch

    def generate_masks(self, N, s, p1, savepath='masks.npy'):
        cell_size = np.ceil(np.array(self.input_size) / s)
        up_size = (s + 1) * cell_size

        grid = np.random.rand(N, s, s) < p1
        grid = grid.astype('float32')

        self.masks = np.empty((N, *self.input_size))

        for i in tqdm(range(N), desc='Generating filters'):
            # Random shifts
            x = np.random.randint(0, cell_size[0])
            y = np.random.randint(0, cell_size[1])
            # Linear upsampling and cropping
            self.masks[i, :, :] = resize(grid[i], up_size, order=1, mode='reflect',
                                         anti_aliasing=False)[x:x + self.input_size[0], y:y + self.input_size[1]]
        self.masks = self.masks.reshape(-1, 1, *self.input_size)
        np.save(savepath, self.masks)
        self.masks = torch.from_numpy(self.masks).float()
        self.masks = self.masks.cuda()
        self.N = N
        self.p1 = p1

    def load_masks(self, filepath, p1, s):
        self.masks = np.load(filepath)
        self.masks = torch.from_numpy(self.masks).float().cuda()
        self.N = self.masks.shape[0]
        self.p1 = p1
        self.s = s


    def visualize_masked_image(self, combined_image, index):
        plt.figure()
        plt.imshow(combined_image, cmap='gray')
        plt.title(f'Masked Image {index}')
        plt.show()


    def save_and_visualize_mutants(self, img_tensor, num_masks, save_folder='mutants'):
        # Ensure the save folder exists
        os.makedirs(save_folder, exist_ok=True)
        
        # Apply torch.mul
        masked_images = torch.mul(self.masks, img_tensor)
        masked_images_cpu = masked_images.cpu().numpy()
        
        # Save each set of 10 mutants into the folder
        for i in range(0, len(masked_images_cpu), 10):
            num_cols = min(10, num_masks - i)
            if num_cols <= 0:
                break
            fig, axes = plt.subplots(1, num_cols, figsize=(20, 4))
            for j in range(num_cols):
                mask = masked_images_cpu[i + j][0]
                axes[j].imshow(mask, cmap='gray')
                axes[j].axis('off')
                axes[j].set_title(f'Mutant {i + j + 1}')
            
            # Save the figure
            plt.savefig(os.path.join(save_folder, f'mutants_{i // 10 + 1}.png'))
            plt.close(fig)
        
        # Interactive visualization
        def show_mutant(index):
            mask = masked_images_cpu[index][0]
            plt.figure()
            plt.imshow(mask, cmap='gray')
            plt.axis('off')
            plt.title(f'Mutant {index + 1}')
            plt.show()
        
        interact(show_mutant, index=IntSlider(min=0, max=num_masks-1, step=1, value=0))
        return masked_images_cpu

    def forward(self, x):
        N = self.N
        _, _, H, W = x.size()
        # Apply array of filters to the image
        stack = torch.mul(self.masks, x.data)
        # p = nn.Softmax(dim=1)(model(stack)) processed in batches
        p = []
        for i in range(0, N, self.gpu_batch):
            p.append(self.model(stack[i:min(i + self.gpu_batch, N)]))
        p = torch.cat(p)
        # Number of classes
        CL = p.size(1)
        sal = torch.matmul(p.data.transpose(0, 1), self.masks.view(N, H * W))
        sal = sal.view((CL, H, W))
        sal = sal / N / self.p1
        return sal
    
    
class RISEBatch(RISE):
    def forward(self, x):
        # Apply array of filters to the image
        N = self.N
        B, C, H, W = x.size()
        stack = torch.mul(self.masks.view(N, 1, H, W), x.data.view(B * C, H, W))
        stack = stack.view(B * N, C, H, W)
        stack = stack

        #p = nn.Softmax(dim=1)(model(stack)) in batches
        p = []
        for i in range(0, N*B, self.gpu_batch):
            p.append(self.model(stack[i:min(i + self.gpu_batch, N*B)]))
        p = torch.cat(p)
        CL = p.size(1)
        p = p.view(N, B, CL)
        sal = torch.matmul(p.permute(1, 2, 0), self.masks.view(N, H * W))
        sal = sal.view(B, CL, H, W)
        return sal

# To process in batches
# def explain_all_batch(data_loader, explainer):
#     n_batch = len(data_loader)
#     b_size = data_loader.batch_size
#     total = n_batch * b_size
#     # Get all predicted labels first
#     target = np.empty(total, 'int64')
#     for i, (imgs, _) in enumerate(tqdm(data_loader, total=n_batch, desc='Predicting labels')):
#         p, c = torch.max(nn.Softmax(1)(explainer.model(imgs.cuda())), dim=1)
#         target[i * b_size:(i + 1) * b_size] = c
#     image_size = imgs.shape[-2:]
#
#     # Get saliency maps for all images in val loader
#     explanations = np.empty((total, *image_size))
#     for i, (imgs, _) in enumerate(tqdm(data_loader, total=n_batch, desc='Explaining images')):
#         saliency_maps = explainer(imgs.cuda())
#         explanations[i * b_size:(i + 1) * b_size] = saliency_maps[
#             range(b_size), target[i * b_size:(i + 1) * b_size]].data.cpu().numpy()
#     return explanations
