import os
#os.environ['TF_ENABLE_AUTO_MIXED_PRECISION'] = '1'
import tensorflow as tf
import numpy as np
from PIL import Image
import random
import shutil

FORMAT = 'NCHW'
BATCH_SIZE = 8

IMG_SIZE = (200,320)

def round_up_div(num,denom):
    return (num+denom-1) // denom

def get_out_dim(dim,level):
    return round_up_div(dim, 2**(level-1))

def get_out_shape(level):
    return (get_out_dim(IMG_SIZE[0],level),get_out_dim(IMG_SIZE[1],level))

def sqr(x):
    return x * x

def unpool(tens4d,factor):
    shape = tens4d.get_shape().as_list()
    spread_shape = [shape[0],shape[1],1,shape[2],1,shape[3]]
    reshaped = tf.reshape(tens4d,spread_shape)
    tiled = tf.tile(reshaped,[1,1,factor,1,factor,1])
    new_shape = [shape[0],shape[1],factor*shape[2],factor*shape[3]]
    back = tf.reshape(tiled,new_shape)
    return back

class Dense:
    def __init__(self,input_dim,out_dim,activation):
        out_shape = [input_dim,out_dim]
        init_vals = tf.initializers.glorot_normal()(out_shape)
        self.weights = tf.Variable(init_vals,name="weights")
        self.biases = tf.Variable(tf.ones(out_dim)*0.01,name="biases")
        self.activation = activation

    def calc(self,input_vec):
        linval = tf.matmul(input_vec,self.weights) + self.biases
        return (linval if self.activation is None else
                    self.activation(linval))

    def vars(self):
        return [self.weights,self.biases]

class Conv2d:
    def __init__(self,input_dim,out_dim,conv_size,activation,strides=[1,1],padding="SAME"):
        assert len(conv_size) == 2,"incorrect conv size"
        out_shape = conv_size+[input_dim]+[out_dim]
        init_vals = tf.initializers.glorot_normal()(out_shape)
        self.weights = tf.Variable(init_vals,name="weights")
        #self.biases = tf.Variable(tf.ones(out_dim)*0.01,name="biases")
        self.activation = activation
        self.strides = strides
        self.padding = padding

    def calc(self,input_vec):
        linval = tf.nn.conv2d(
            input=input_vec,
            filter=self.weights,
            strides=self.strides,
            data_format=FORMAT,
            padding=self.padding)
        #affine_val = linval + self.biases
        activated = (linval if self.activation is None else
                    self.activation(linval))
        return activated

    def vars(self):
        return [self.weights,self.biases]


def Conv1x1(input_dim,out_dim,activation):
    return Conv2d(input_dim,out_dim,[1,1],activation)

def Conv1x1Upsample(input_dim,out_dim,activation,out_shape,upsample_factor):
    return ConvTrans2d(input_dim,out_dim,[1,1],activation,out_shape,strides=[upsample_factor,upsample_factor])


class ConvTrans2d:
    def __init__(self,input_dim,out_dim,conv_size,activation,out_shape,strides=[1,1],padding="SAME"):
        assert len(conv_size) == 2,"incorrect conv size"
        filter_shape = conv_size+[out_dim]+[input_dim]
        init_vals = tf.initializers.glorot_normal()(filter_shape)
        self.weights = tf.Variable(init_vals,name="weights")
        #self.biases = tf.Variable(tf.ones(out_dim)*0.01,name="biases")
        self.activation = activation
        self.strides = strides
        self.padding = padding
        self.out_dim = out_dim
        self.out_shape = out_shape


    def calc(self,input_vec):
        in_shape = input_vec.get_shape().as_list()
        out_shape = [
            in_shape[0],
            self.out_dim,
            self.out_shape[0],
            self.out_shape[1],
        ]
        linval = tf.nn.conv2d_transpose(
            value=input_vec,
            filter=self.weights,
            output_shape=out_shape,
            strides=self.strides,
            data_format=FORMAT)
        #affine_val = linval + self.biases
        activated = (linval if self.activation is None else
                    self.activation(linval))
        return activated

    def vars(self):
        return [self.weights,self.biases]

def avgpool2d(input,window_shape):
    return tf.nn.pool(input,
        window_shape=window_shape,
        pooling_type="AVG",
        padding="SAME",
        strides=window_shape,
        )

def default_activ(input):
    return tf.nn.relu(input)


class Convpool2:
    def __init__(self,in_dim,out_dim,out_activ,use_batchnorm=True):
        self.CONV_SIZE = [3,3]
        self.POOL_SHAPE = [2,2]
        self.out_activ = out_activ
        self.use_batchnorm = use_batchnorm
        #self.bn1 = tf.layers.BatchNormalization(momentum=0.9)
        #if self.use_batchnorm:
        #    self.bn2 = tf.layers.BatchNormalization(momentum=0.9)
        self.conv1 = Conv2d(in_dim,out_dim,self.CONV_SIZE,None)
        self.conv2 = Conv2d(out_dim,out_dim,self.CONV_SIZE,None,strides=self.POOL_SHAPE)

    def calc(self,in_vec):
        cur_vec = in_vec
        cur_vec = self.conv1.calc(cur_vec)
        #cur_vec = self.bn1(cur_vec)
        cur_vec = default_activ(cur_vec)
        cur_vec = self.conv2.calc(cur_vec)
        #if self.use_batchnorm:
        #    cur_vec = self.bn2(cur_vec)
        if self.out_activ is not None:
            cur_vec = self.out_activ(cur_vec)
        #cur_vec = avgpool2d(cur_vec,self.POOL_SHAPE)
        return cur_vec


class Deconv2:
    def __init__(self,in_dim,out_dim,out_activ,out_shape):
        self.CONV_SIZE = [3,3]
        self.POOL_SHAPE = [2,2]
        self.conv1 = ConvTrans2d(in_dim,in_dim,self.CONV_SIZE,default_activ,out_shape,strides=self.POOL_SHAPE)
        self.conv2 = ConvTrans2d(in_dim,out_dim,self.CONV_SIZE,out_activ,out_shape)

    def calc(self,in_vec):
        cur_vec = in_vec
        cur_vec = self.conv1.calc(cur_vec)
        cur_vec = self.conv2.calc(cur_vec)
        #cur_vec = avgpool2d(cur_vec,self.POOL_SHAPE)
        return cur_vec


def distances(inputs,vecs):
    #return tf.matmul(vecs1,vecs2,transpose_b=True)
    #vecs = tf.transpose(vecs,perm=[1,0,2])
    matmul_val = tf.einsum("ijk,jmk->ijm",inputs,vecs)
    sum_sqr_input = tf.reduce_sum(sqr(inputs), axis=-1, keepdims=True)
    sum_sqr_vecs = tf.reduce_sum(sqr(vecs), axis=-1, keepdims=False)
    dists = (sum_sqr_input
             - 2 * matmul_val
             + sum_sqr_vecs)
    return dists

def gather_multi_idxs(qu_vecs,chosen_idxs):
    idx_shape = chosen_idxs.get_shape().as_list()
    qu_shape = qu_vecs.get_shape().as_list()
    idx_add = tf.range(qu_shape[0],dtype=tf.int64)*qu_shape[1] + chosen_idxs
    idx_transform = tf.reshape(idx_add,[prod(idx_shape)])
    rqu_vecs = tf.reshape(qu_vecs,[qu_shape[0]*qu_shape[1],qu_shape[2]])

    closest_vec_values = tf.gather(rqu_vecs,idx_transform,axis=0)
    #combined_vec_vals = tf.gather(qu_vecs,chosen_idxs,axis=0)

    combined_vec_vals = tf.reshape(closest_vec_values,[idx_shape[0],qu_shape[0]*qu_shape[2]])

    return combined_vec_vals

@tf.custom_gradient
def quant_calc(qu_vecs,chosen_idxs,in_vecs):
    closest_vec_values = gather_multi_idxs(qu_vecs,chosen_idxs)

    def grad(dy):
        return tf.zeros_like(qu_vecs),tf.zeros_like(chosen_idxs),dy
    return closest_vec_values,grad

def assign_moving_average(var,cur_val,decay):
    new_var = var * decay + cur_val * (1-decay)
    print(new_var.shape)
    print(var.shape)
    update = tf.assign(var,new_var)
    return new_var,update


class QuantBlock:
    def __init__(self,QUANT_SIZE,NUM_QUANT,QUANT_DIM):
        init_vals = tf.random_normal([NUM_QUANT,QUANT_SIZE,QUANT_DIM],dtype=tf.float32)
        self.vectors = tf.Variable(init_vals,name="vecs")
        self.vector_counts = tf.Variable(tf.zeros(shape=[NUM_QUANT,QUANT_SIZE],dtype=tf.float32),name="vecs")

        self._ema_cluster_size = tf.Variable(tf.zeros([NUM_QUANT,QUANT_SIZE],dtype=tf.float32))
        #ema_init = tf.reshape(init_vals,[QUANT_SIZE,QUANT_DIM])
        self._ema_w = tf.Variable(init_vals,name='ema_dw')

        self.QUANT_SIZE = QUANT_SIZE
        self.QUANT_DIM = QUANT_DIM
        self.NUM_QUANT = NUM_QUANT
        self._decay = 0.9
        self._epsilon=1e-5

    def calc(self, input):
        orig_size = input.get_shape().as_list()
        div_input = tf.reshape(input,[orig_size[0],self.NUM_QUANT,self.QUANT_DIM])
        #dists = tf.einsum("ijk,jmk->ijm",div_input,self.vectors)
        dists = distances(div_input,self.vectors)
        #cluster_size = tf.reshape(,[self.QUANT_SIZE])
        dists = dists * (1.0+(tf.sqrt(self._ema_cluster_size)))

        #dists = distances(input,self.vectors)
        #soft_vals = tf.softmax(,axis=1)
        #inv_dists = 1.0/(dists+0.000001)
        #closest_vec_idx = tf.multinomial((inv_dists),1)
        #closest_vec_idx = tf.reshape(closest_vec_idx,shape=[closest_vec_idx.get_shape().as_list()[0]])
        #print(closest_vec_idx.shape)
        closest_vec_idx = tf.argmin(dists,axis=-1)

        out_val = quant_calc(self.vectors,closest_vec_idx,input)
        other_losses, update = self.calc_other_vals(input,closest_vec_idx)
        return out_val, other_losses, update

    def codebook_update(self,input,closest_vec_idxs):
        #BATCH_SIZE = input.get_shape().as_list()[0]
        #closest_vec_idxs = tf.reshape(closest_vec_idxs,[BATCH_SIZE,])
        closest_vec_onehots = tf.one_hot(closest_vec_idxs,self.QUANT_SIZE)

        updated_ema_cluster_size,cluster_update = assign_moving_average(
          self._ema_cluster_size, tf.reduce_sum(closest_vec_onehots, axis=0), self._decay)
        dw = tf.einsum("ijk,ijm->jmk",input,closest_vec_onehots)#tf.matmul(input, closest_vec_onehots, transpose_a=True)
        #dw = tf.transpose(dw)
        #print(dw)
        #dw = closest_vec_vals
        #exit(0)
        updated_ema_w,ema_w_update = assign_moving_average(self._ema_w, dw,
                                                            self._decay)
        n = tf.reduce_sum(updated_ema_cluster_size)
        updated_ema_cluster_size = (
          (updated_ema_cluster_size + self._epsilon)
          / (n + self.QUANT_SIZE * self._epsilon) * n)

        normalised_updated_ema_w = (
          updated_ema_w / tf.reshape(updated_ema_cluster_size, [self.NUM_QUANT,self.QUANT_SIZE,1]))
        #update_reshaped = tf.reshape(normalised_updated_ema_w,[self.NUM_QUANT,self.QUANT_SIZE,self.QUANT_DIM])
        update_w = tf.assign(self.vectors, normalised_updated_ema_w)

        all_updates = tf.group([update_w,ema_w_update,cluster_update])
        return all_updates#all_updates

    def calc_other_vals(self,input,closest_vec_idx):
        closest_vec_values = gather_multi_idxs(self.vectors,closest_vec_idx)

        #codebook_loss = tf.reduce_sum(sqr(closest_vec_values - tf.stop_gradient(input)))
        orig_size = input.get_shape().as_list()
        div_input = tf.reshape(input,[orig_size[0],self.NUM_QUANT,self.QUANT_DIM])
        codebook_update = self.codebook_update(div_input,closest_vec_idx)

        beta_val = 0.25 #from https://arxiv.org/pdf/1906.00446.pdf
        commitment_loss = tf.reduce_sum(beta_val * sqr(tf.stop_gradient(closest_vec_values) - input))

        idx_one_hot = tf.one_hot(closest_vec_idx,self.QUANT_SIZE)
        total = tf.reduce_sum(idx_one_hot,axis=0)
        update_counts = tf.assign(self.vector_counts,self.vector_counts+total)
        combined_update = tf.group([codebook_update,update_counts])

        return commitment_loss ,combined_update

    def resample_bad_vecs(self):
        sample_vals = tf.random_normal([self.NUM_QUANT,self.QUANT_SIZE,self.QUANT_DIM],dtype=tf.float32)
        equal_vals = tf.cast(tf.equal(self.vector_counts,0),dtype=tf.float32)
        equal_vals= tf.reshape(equal_vals,shape=[self.NUM_QUANT,self.QUANT_SIZE,1])
        new_vecs = self.vectors - self.vectors * equal_vals + sample_vals * equal_vals
        vec_assign = tf.assign(self.vectors,new_vecs)
        ema_assign = tf.assign(self._ema_w,new_vecs)
        zero_assign = tf.assign(self.vector_counts,tf.zeros_like(self.vector_counts))
        tot_assign = tf.group([vec_assign,zero_assign,ema_assign])
        return zero_assign#tot_assign

def prod(l):
    p = 1
    for x in l:
        p *= x
    return p

class QuantBlockImg(QuantBlock):
    def calc(self,input):
        input = tf.transpose(input,perm=(0,2,3,1))
        in_shape = input.get_shape().as_list()
        flat_val = tf.reshape(input,[prod(in_shape[:3]),in_shape[3]])
        out,o1,o2 = QuantBlock.calc(self,flat_val)
        restored = tf.reshape(out,in_shape)
        restored = tf.transpose(restored,perm=(0,3,1,2))
        return restored,o1,o2

IMG_LEVEL = 32
SECOND_LEVEL = 64
THIRD_LEVEL = 128
FOURTH_LEVEL = 192
FIFTH_LEVEL = 256
ZIXTH_LEVEL = 256
class MainCalc:
    def __init__(self):
        self.convpool1 = Convpool2(3,IMG_LEVEL,default_activ)
        self.convpool2 = Convpool2(IMG_LEVEL,SECOND_LEVEL,None)
        self.convpool3 = Convpool2(SECOND_LEVEL,THIRD_LEVEL,default_activ)
        self.convpool4 = Convpool2(THIRD_LEVEL,FOURTH_LEVEL,None)
        self.convpool5 = Convpool2(FOURTH_LEVEL,FIFTH_LEVEL,default_activ)
        self.convpool6 = Convpool2(FIFTH_LEVEL,ZIXTH_LEVEL,None)

        self.quanttrans1 = Conv1x1(SECOND_LEVEL,SECOND_LEVEL,None)
        self.quant_block1 = QuantBlockImg(256,1,SECOND_LEVEL)
        self.quanttrans2 = Conv1x1(FOURTH_LEVEL,FOURTH_LEVEL,None)
        self.quant_block2 = QuantBlockImg(256,4,FOURTH_LEVEL//4)
        self.quant_block3 = QuantBlockImg(256,4,ZIXTH_LEVEL//4)

        self.deconv6 = Deconv2(ZIXTH_LEVEL,FIFTH_LEVEL,default_activ,get_out_shape(6))
        self.deconv5 = Deconv2(FIFTH_LEVEL,FOURTH_LEVEL,default_activ,get_out_shape(5))
        self.deconv4 = Deconv2(FOURTH_LEVEL,THIRD_LEVEL,default_activ,get_out_shape(4))
        self.deconv3 = Deconv2(THIRD_LEVEL,SECOND_LEVEL,default_activ,get_out_shape(3))
        self.deconv2 = Deconv2(SECOND_LEVEL,IMG_LEVEL,default_activ,get_out_shape(2))
        self.deconv1 = Deconv2(IMG_LEVEL,3,tf.sigmoid,get_out_shape(1))

        self.quantdownsample32 = Conv1x1Upsample(ZIXTH_LEVEL,FOURTH_LEVEL,None,get_out_shape(5),4)
        self.quantdownsample31 = Conv1x1Upsample(ZIXTH_LEVEL,SECOND_LEVEL,None,get_out_shape(3),16)
        self.quantdownsample21 = Conv1x1Upsample(FOURTH_LEVEL,SECOND_LEVEL,None,get_out_shape(3),4)

        self.bn1a = tf.layers.BatchNormalization(axis=1)
        self.bn1b = tf.layers.BatchNormalization(axis=1)
        self.bn2a = tf.layers.BatchNormalization(axis=1)
        self.bn2b = tf.layers.BatchNormalization(axis=1)
        self.bn3 = tf.layers.BatchNormalization(axis=1)
        #self.deconvbn2 = tf.layers.BatchNormalization()

    def calc(self,input):
        out1 = self.convpool1.calc(input)
        out2 = self.convpool2.calc(out1)
        out3 = self.convpool3.calc(out2)
        out4 = self.convpool4.calc(out3)
        out5 = self.convpool5.calc(out4)
        out6 = self.convpool6.calc(out5)

        quant3,quant_loss3,update3 = self.quant_block3.calc((self.bn3(out6,training=True)*0.5))
        dec6 = self.deconv6.calc(quant3)
        dec5 = self.deconv5.calc(dec6)
        out4trans = self.quanttrans2.calc(out4)

        quant2,quant_loss2,update2 = self.quant_block2.calc(((self.bn1a(dec5,training=True)+self.bn1b(out4trans,training=True)))*0.5)
        dec4 = self.deconv4.calc(quant2+self.quantdownsample32.calc(quant3))
        dec3 = self.deconv3.calc(dec4)
        out2trans = self.quanttrans1.calc(out2)
        quant1,quant_loss1,update1 = self.quant_block1.calc(self.bn2a(dec3,training=True)+self.bn2b(out2trans,training=True))
        dec2 = self.deconv2.calc(quant1+self.quantdownsample21.calc(quant2)+self.quantdownsample31.calc(quant3))
        dec1 = self.deconv1.calc(dec2)
        decoded_final = dec1

        reconstr_loss = tf.reduce_sum(sqr(decoded_final - input))

        quant_loss = quant_loss1 + quant_loss2 + quant_loss3
        tot_loss = reconstr_loss + quant_loss
        tot_update = tf.group([update1,update2,update3])
        #losest_list = [closest1]#,closest2,closest3]
        return tot_update,tot_loss, reconstr_loss,decoded_final

    # def __init__(self):
    #     self.convpool1 = Convpool2(3,64,default_activ)
    #     self.convpool2 = Convpool2(64,128,None)
    #     self.quant_block = QuantBlockImg(64,4,32)
    #     self.convunpool1 = Deconv2(128,64,default_activ)
    #     self.convunpool2 = Deconv2(64,3,tf.nn.sigmoid)
    #
    #
    # def calc(self,input):
    #     out1 = self.convpool1.calc(input)
    #     out2 = self.convpool2.calc(out1)
    #     quant,quant_loss,update = self.quant_block.calc(out2)
    #     print(quant.shape)
    #     dec1 = self.convunpool1.calc(quant)
    #     decoded_final = self.convunpool2.calc(dec1)
    #
    #     reconstr_loss = tf.reduce_sum(sqr(decoded_final - input))
    #
    #     tot_loss = reconstr_loss + quant_loss
    #     return update,tot_loss, reconstr_loss,decoded_final

    def periodic_update(self):
        return tf.group([
            self.quant_block1.resample_bad_vecs(),
            self.quant_block2.resample_bad_vecs(),
            self.quant_block3.resample_bad_vecs(),
        ])

mc = MainCalc()
place = tf.placeholder(shape=[BATCH_SIZE,3,200,320],dtype=tf.float32)

optimizer = tf.train.AdamOptimizer(learning_rate=0.0001)

mc_update, loss, reconst_l, final_output = mc.calc(place)
resample_update = mc.periodic_update()

batchnorm_updates = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
print(batchnorm_updates)
comb_updates = tf.group(batchnorm_updates)
tot_update = tf.group([mc_update,comb_updates])

opt = optimizer.minimize(loss)
orig_imgs = []
orig_filenames = []
for img_name in os.listdir("data/input_data"):
    with Image.open("data/input_data/"+img_name) as img:
        if img.mode == "RGB":
            arr = np.array(img)
            arr = np.transpose(arr,(2,0,1))
            orig_imgs.append(arr.astype(np.float32)/256.0)
            orig_filenames.append(img_name)

fold_names = [fname.split('.')[0]+"/" for fname in orig_filenames[:50]]

for fold,fname in zip(fold_names,orig_filenames):
    fold_path = "data/result/"+fold
    os.makedirs(fold_path,exist_ok=True)
    shutil.copy("data/input_data/"+fname,fold_path+"org.jpg")

imgs = [img for img in orig_imgs]
saver = tf.train.Saver(max_to_keep=50)
SAVE_DIR = "data/save_model/"
os.makedirs(SAVE_DIR,exist_ok=True)
SAVE_NAME = SAVE_DIR+"model.ckpt"
logfilename = "data/count_log.txt"
logfile = open(logfilename,'w')

config = tf.ConfigProto()
config.gpu_options.allow_growth=True
with tf.Session(config=config) as sess:
    sess.run(tf.global_variables_initializer())
    print_num = 0
    lossval_num = 0
    if os.path.exists(SAVE_DIR+"checkpoint"):
        print("reloaded")
        checkpoint = tf.train.latest_checkpoint(SAVE_DIR)
        print(checkpoint)
        print_num = int(checkpoint.split('-')[1])
        saver.restore(sess, checkpoint)

    batch = []
    batch_count = 0
    while True:
        for x in range(20):
            random.shuffle(imgs)
            tot_loss = 0
            rec_loss = 0
            loss_count = 0
            for img in imgs:
                batch.append(img)
                if len(batch) == BATCH_SIZE:
                    batch_count += 1
                    _,_,cur_loss,cur_rec = sess.run([tot_update,opt,loss,reconst_l],feed_dict={
                        place:np.stack(batch)
                    })
                    loss_count += 1
                    tot_loss += cur_loss
                    rec_loss += cur_rec
                    batch = []

                    EPOC_SIZE = 100
                    if batch_count % EPOC_SIZE == 0:
                        print("epoc ended, loss: {}   {}".format(tot_loss/loss_count,rec_loss/loss_count))
                        lossval_num += 1
                        logfile.write("counts step {} quant 1".format(lossval_num))
                        logfile.write(",".join([str(val.astype(np.int64)) for val in sess.run(mc.quant_block1.vector_counts)])+"\n")
                        logfile.write("counts step {} quant 2".format(lossval_num))
                        logfile.write(",".join([str(val.astype(np.int64)) for val in sess.run(mc.quant_block2.vector_counts)])+"\n")
                        logfile.write("counts step {} quant 3".format(lossval_num))
                        logfile.write(",".join([str(val.astype(np.int64)) for val in sess.run(mc.quant_block3.vector_counts)])+"\n")
                        logfile.flush()
                        sess.run(resample_update)

                        tot_loss = 0
                        rec_loss = 0
                        loss_count = 0

                        if batch_count % (EPOC_SIZE*10) == 0:
                            print_num += 1
                            print("save {} started".format(print_num))
                            saver.save(sess,SAVE_NAME,global_step=print_num)
                            img_batch = []
                            fold_batch = []
                            for count,(img,fold) in enumerate(zip(orig_imgs,fold_names)):
                                img_batch.append((img))
                                fold_batch.append((fold))
                                if len(img_batch) == BATCH_SIZE:
                                    batch_outs = sess.run(final_output,feed_dict={
                                        place:np.stack(img_batch)
                                    })
                                    pixel_vals = (batch_outs * 256).astype(np.uint8)
                                    for out,out_fold in zip(pixel_vals,fold_batch):
                                        #print(out.shape)
                                        out = np.transpose(out,(1,2,0))
                                        img = Image.fromarray(out)
                                        img_path = "data/result/{}{}.jpg".format(out_fold,print_num)
                                        #print(img_path)
                                        img.save(img_path)
                                    img_batch = []
                                    fold_batch = []
                            print("save {} finished".format(print_num))


#print(out.shape)
