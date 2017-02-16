from __future__ import division
from scipy.misc import imread, imresize, imsave, fromimage, toimage
from scipy.optimize import fmin_l_bfgs_b
import numpy as np
import time
from keras.models import Sequential
from keras.layers.convolutional import Convolution2D, AveragePooling2D, MaxPooling2D
from keras import backend as K
from keras.utils.data_utils import get_file


# pretrained models to load
THEANO_WEIGHTS_PATH_NO_TOP = 'https://github.com/fchollet/deep-learning-models/releases/download/v0.1/vgg16_weights_th_dim_ordering_th_kernels_notop.h5'
TF_WEIGHTS_PATH_NO_TOP = 'https://github.com/fchollet/deep-learning-models/releases/download/v0.1/vgg16_weights_tf_dim_ordering_tf_kernels_notop.h5'

# content, style, and result image info
base_image_path = "<path to base image>"
style_reference_image_path = "<path to style image>"

# hyperparameters
num_iter = 50
rescale_image = True
maintain_aspect_ratio = True
preserve_color = False
pooltype = "max"  # max or ave

# weights of the different loss components
total_variation_weight = 8.5e-5
style_weight = 1.0
content_weight = .05

# dimensions of the generated picture.
img_width = img_height = 400
img_WIDTH = img_HEIGHT = 0
aspect_ratio = 0


# util function to open, resize and format pictures into appropriate tensors
def preprocess_image(image_path, load_dims=False):
    global img_WIDTH, img_HEIGHT, aspect_ratio

    img = imread(image_path, mode="RGB")  # Prevents crashes due to PNG images (ARGB)
    if load_dims:
        img_WIDTH = img.shape[0]
        img_HEIGHT = img.shape[1]
        aspect_ratio = img_HEIGHT / img_WIDTH

    img = imresize(img, (img_width, img_height))
    img = img[:, :, ::-1].astype('float32')
    img[:, :, 0] -= 103.939
    img[:, :, 1] -= 116.779
    img[:, :, 2] -= 123.68

    if K.image_dim_ordering() == "th":
        img = img.transpose((2, 0, 1)).astype('float32')

    img = np.expand_dims(img, axis=0)
    return img


# util function to convert a tensor into a valid image
def deprocess_image(x):
    if K.image_dim_ordering() == "th":
        x = x.transpose((1, 2, 0))

    x[:, :, 0] += 103.939
    x[:, :, 1] += 116.779
    x[:, :, 2] += 123.68
    x = x[:, :, ::-1]
    x = np.clip(x, 0, 255).astype('uint8')
    return x


# util function to preserve image color
def original_color_transform(content, generated):
    generated = fromimage(toimage(generated, mode='RGB'), mode='YCbCr')  # Convert to YCbCr color space
    generated[:, :, 1:] = content[:, :, 1:]  # Generated CbCr = Content CbCr
    generated = fromimage(toimage(generated, mode='YCbCr'), mode='RGB')  # Convert to RGB color space
    return generated

# pooling function
pooltype = 1 if pooltype == "ave" else 0


def pooling_func():
    if pooltype == 1:
        return AveragePooling2D((2, 2), strides=(2, 2))
    else:
        return MaxPooling2D((2, 2), strides=(2, 2))


# get tensor representations of images
base_image = K.variable(preprocess_image(base_image_path, True))
style_reference_image = K.variable(preprocess_image(style_reference_image_path))

# placeholder for generated image
if K.image_dim_ordering() == 'th':
    combination_image = K.placeholder((1, 3, img_width, img_height))
else:
    combination_image = K.placeholder((1, img_width, img_height, 3))

# combine the 3 images into a single Keras tensor
input_tensor = K.concatenate([base_image,
                              style_reference_image,
                              combination_image], axis=0)

if K.image_dim_ordering() == "th":
    shape = (3, 3, img_width, img_height)
else:
    shape = (3, img_width, img_height, 3)

# build the VGG16 network with 3 images as input
first_layer = Convolution2D(64, 3, 3, activation='relu', name='conv1_1')
first_layer.set_input(input_tensor, shape)

model = Sequential()
model.add(first_layer)
model.add(Convolution2D(64, 3, 3, activation='relu', name='conv1_2', border_mode='same'))
model.add(pooling_func())

model.add(Convolution2D(128, 3, 3, activation='relu', name='conv2_1', border_mode='same'))
model.add(Convolution2D(128, 3, 3, activation='relu', name='conv2_2', border_mode='same'))
model.add(pooling_func())

model.add(Convolution2D(256, 3, 3, activation='relu', name='conv3_1', border_mode='same'))
model.add(Convolution2D(256, 3, 3, activation='relu', name='conv3_2', border_mode='same'))
model.add(Convolution2D(256, 3, 3, activation='relu', name='conv3_3', border_mode='same'))
model.add(pooling_func())

model.add(Convolution2D(512, 3, 3, activation='relu', name='conv4_1', border_mode='same'))
model.add(Convolution2D(512, 3, 3, activation='relu', name='conv4_2', border_mode='same'))
model.add(Convolution2D(512, 3, 3, activation='relu', name='conv4_3', border_mode='same'))
model.add(pooling_func())

model.add(Convolution2D(512, 3, 3, activation='relu', name='conv5_1', border_mode='same'))
model.add(Convolution2D(512, 3, 3, activation='relu', name='conv5_2', border_mode='same'))
model.add(Convolution2D(512, 3, 3, activation='relu', name='conv5_3', border_mode='same'))
model.add(pooling_func())

if K.image_dim_ordering() == "th":
    weights = get_file('vgg16_weights_th_dim_ordering_th_kernels_notop.h5', THEANO_WEIGHTS_PATH_NO_TOP)
else:
    weights = get_file('vgg16_weights_tf_dim_ordering_tf_kernels_notop.h5', TF_WEIGHTS_PATH_NO_TOP)

model.load_weights(weights)

print 'Loaded model'

outputs_dict = dict([(layer.name, layer.output) for layer in model.layers])


# the gram matrix of an image tensor (feature-wise outer product) using shifted activations
def gram_matrix(x):
    assert K.ndim(x) == 3
    features = K.batch_flatten(x)
    gram = K.dot(features - 1, K.transpose(features - 1))
    return gram


# style loss
def style_loss(style, combination):
    assert K.ndim(style) == 3
    assert K.ndim(combination) == 3
    S = gram_matrix(style)
    C = gram_matrix(combination)
    channels = 3
    size = img_width * img_height
    return K.sum(K.square(S - C)) / (4. * (channels ** 2) * (size ** 2))


# maintains the "content" of the base image
def content_loss(base, combination):
    return K.sum(K.square(combination - base))


def total_variation_loss(x):
    assert K.ndim(x) == 4
    if K.image_dim_ordering() == 'th':
        a = K.square(x[:, :, :img_width - 1, :img_height - 1] - x[:, :, 1:, :img_height - 1])
        b = K.square(x[:, :, :img_width - 1, :img_height - 1] - x[:, :, :img_width - 1, 1:])
    else:
        a = K.square(x[:, :img_width - 1, :img_height - 1, :] - x[:, 1:, :img_height - 1, :])
        b = K.square(x[:, :img_width - 1, :img_height - 1, :] - x[:, :img_width - 1, 1:, :])
    return K.sum(K.pow(a + b, 1.25))


feature_layers = ['conv1_1', 'conv1_2', 'conv2_1', 'conv2_2', 'conv3_1', 'conv3_2', 'conv3_3',
                  'conv4_1', 'conv4_2', 'conv4_3', 'conv5_1', 'conv5_2', 'conv5_3']

# combine these loss functions into a single scalar
loss = K.variable(0.)
layer_features = outputs_dict["conv5_2"]
base_image_features = layer_features[0, :, :, :]
combination_features = layer_features[2, :, :, :]
loss += content_weight * content_loss(base_image_features,
                                      combination_features)

# Use all layers for style feature extraction and reconstruction
nb_layers = len(feature_layers) - 1

# Chained Inference without blurring
for i in range(len(feature_layers) - 1):
    layer_features = outputs_dict[feature_layers[i]]
    style_reference_features = layer_features[1, :, :, :]
    combination_features = layer_features[2, :, :, :]
    sl1 = style_loss(style_reference_features, combination_features)

    layer_features = outputs_dict[feature_layers[i + 1]]
    style_reference_features = layer_features[1, :, :, :]
    combination_features = layer_features[2, :, :, :]
    sl2 = style_loss(style_reference_features, combination_features)

    sl = sl1 - sl2

    # Geometric weighted scaling of style loss
    loss += (style_weight / (2 ** (nb_layers - (i + 1)))) * sl

loss += total_variation_weight * total_variation_loss(combination_image)
grads = K.gradients(loss, combination_image)

outputs = [loss]
if type(grads) in {list, tuple}:
    outputs += grads
else:
    outputs.append(grads)

f_outputs = K.function([combination_image], outputs)


def eval_loss_and_grads(x):
    if K.image_dim_ordering() == 'th':
        x = x.reshape((1, 3, img_width, img_height))
    else:
        x = x.reshape((1, img_width, img_height, 3))
    outs = f_outputs([x])
    loss_value = outs[0]
    if len(outs[1:]) == 1:
        grad_values = outs[1].flatten().astype('float64')
    else:
        grad_values = np.array(outs[1:]).flatten().astype('float64')
    return loss_value, grad_values


# compute loss and gradients
class Evaluator(object):
    def __init__(self):
        self.loss_value = None
        self.grads_values = None

    def loss(self, x):
        assert self.loss_value is None
        loss_value, grad_values = eval_loss_and_grads(x)
        self.loss_value = loss_value
        self.grad_values = grad_values
        return self.loss_value

    def grads(self, x):
        assert self.loss_value is not None
        grad_values = np.copy(self.grad_values)
        self.loss_value = None
        self.grad_values = None
        return grad_values


evaluator = Evaluator()

# run scipy-based optimization (L-BFGS) over the pixels of the generated image
# so as to minimize the neural style loss


x = preprocess_image(base_image_path, True)

# We require original image if we are to preserve color in YCbCr mode
if preserve_color:
    content = imread(base_image_path, mode="YCbCr")
    content = imresize(content, (img_width, img_height))

prev_min_val = np.inf

improvement_threshold = 0.0

for i in range(num_iter):
    print "Start of iteration {0}".format(i + 1)
    start_time = time.time()

    x, min_val, info = fmin_l_bfgs_b(evaluator.loss, x.flatten(), fprime=evaluator.grads, maxfun=20)

    improvement = (prev_min_val - min_val) / prev_min_val * 100

    print "Current loss value:", min_val, " Improvement : %0.3f" % improvement, "%"
    prev_min_val = min_val
    # save current generated image
    img = deprocess_image(x.copy().reshape((3, img_width, img_height)))

    if preserve_color and content is not None:
        img = original_color_transform(content, img)

    if maintain_aspect_ratio & (not rescale_image):
        img_ht = int(img_width * aspect_ratio)
        print "Rescaling Image to (%d, %d)" % (img_width, img_ht)
        img = imresize(img, (img_width, img_ht), interp="bilinear")

    if rescale_image:
        print "Rescaling Image to (%d, %d)" % (img_WIDTH, img_HEIGHT)
        img = imresize(img, (img_WIDTH, img_HEIGHT), interp="bilinear")
    
    fname = "result_at_iteration_%d.png" % (i + 1)
    imsave(fname, img)
    end_time = time.time()
    print "Image saved as", fname
    print "Iteration %d completed in %ds" % (i + 1, end_time - start_time)

    if improvement_threshold is not 0.0:
        if improvement < improvement_threshold and improvement is not np.nan:
            print "Improvement (%f) is less than improvement threshold (%f). Early stopping script." % (improvement, improvement_threshold)
            exit()
