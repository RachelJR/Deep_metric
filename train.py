# coding=utf-8
from __future__ import absolute_import, print_function
import argparse
import os
import sys
import torch.utils.data
from torch.backends import cudnn
import torch.optim as optim
from torch.autograd import Variable
import models
import losses
from utils import RandomIdentitySampler, mkdir_if_missing, logging, orth_reg, chars2nums
import DataSet
cudnn.benchmark = True

parser = argparse.ArgumentParser(description='PyTorch Training')
parser.add_argument('-data', default='car', required=True,
                    help='path to dataset')
parser.add_argument('-loss', default='branch', required=True,
                    help='loss for training network')
parser.add_argument('-m', default=0.5,type=float, required=False,
                   help='margin in loss function')
parser.add_argument('-nums', default=None, type=list, required=False)
parser.add_argument('-net', default='bn',
                    help='network used')
parser.add_argument('-init', default=None,
                    help='the way of weight initialization')
parser.add_argument('-r', default=None,
                    help='the path of the pre-trained model')
parser.add_argument('-start', default=0, type=int,
                    help='resume epoch')

parser.add_argument('-log_dir', default=None,
                    help='where the trained models save')

parser.add_argument('-BatchSize', '-b', default=128, type=int, metavar='N',
                    help='mini-batch size (1 = pure stochastic) Default: 256')
parser.add_argument('-num_instances', default=4, type=int, metavar='n',
                    help='the number of samples from one class in mini-batch')
parser.add_argument('-dim', default=512, type=int, metavar='n',
                    help='the dimension of embedding space')


parser.add_argument('-epochs', default=400, type=int, metavar='N',
                    help='epochs for training process')
parser.add_argument('-step', '-s', default=100, type=int, metavar='N',
                    help='number of epochs to decay learn rate')
parser.add_argument('-save_step', default=40, type=int, metavar='N',
                    help='number of epochs to save model')
# optimizer
parser.add_argument('-lr', type=float, default=1e-4,
                    help="learning rate of new parameters")
parser.add_argument('-base', type=float, default=0.1,
                    help="the multiplier for learning rate of pre-trained  parameters")
parser.add_argument('--nThreads', '-j', default=4, type=int, metavar='N',
                    help='number of data loading threads (default: 2)')
parser.add_argument('--momentum', type=float, default=0.9)
parser.add_argument('--weight-decay', type=float, default=2e-4)
parser.add_argument('-orth_cof', type=float, default=0e-3,
                    help='try to make the last linear weight matrix to '
                         'approximate the orthogonal matrix')

args = parser.parse_args()

print(args.nums)
print(type(args.nums))

if args.log_dir is None:
    log_dir = os.path.join('checkpoints', args.loss)
else:
    log_dir = os.path.join('checkpoints', args.log_dir)
mkdir_if_missing(log_dir)
# write log
sys.stdout = logging.Logger(os.path.join(log_dir, 'log.txt'))

#  display information of current training
print('train on dataset %s' % args.data)
print('batch size is: %d' % args.BatchSize)
print('num_instance is %d' % args.num_instances)
print('dimension of the embedding space is %d' % args.dim)
print('log dir is: %s' % args.log_dir)
print('the network is : %s' % args.net)
print('loss function for training is: %s' % args.loss)
print('learn rate : %f' % args.lr)
print('base parameter de learn rate is : %f' % args.base)
print('the orthogonal weight regular is %f ' % args.orth_cof)

#  load pretrained models
if args.r is not None:
    model = torch.load(args.r)
else:
    model = models.create(args.net, Embed_dim=args.dim)

    # load part of the model
    model_dict = model.state_dict()
    # print(model_dict)

    if args.net == 'bn' or args.net == 'branch':
        pretrained_dict = torch.load('pretrained_models/bn_inception-239d2248.pth')
    else:
        pretrained_dict = torch.load('pretrained_models/inception_v3_google-1a9a5a14.pth')

    pretrained_dict = {k: v for k, v in pretrained_dict.items() if k in model_dict}

    model_dict.update(pretrained_dict)

    if args.init == 'orth':
        # initialization of last linear weight
        print('initialize the network orthogonally -----------   hello wangxiaowu! come on!!')
        _, _, v = torch.svd(model_dict['Embed.linear.weight'])
        model_dict['Embed.linear.weight'] = v.t()
        model_dict['Embed.linear.bias'] = torch.zeros(args.dim)
    elif args.init == 'norm':
        print('initialize the network normally -----------   hello wangxiaowu! come on!!')
        w = model_dict['Embed.linear.weight']
        norm = w.norm(dim=1, p=2, keepdim=True)
        w = w.div(norm.expand_as(w))
        model_dict['Embed.linear.weight'] = w
        model_dict['Embed.linear.bias'] = torch.zeros(args.dim)
    elif args.init == 'rand':
        model_dict['Embed.linear.bias'] = torch.zeros(args.dim)
        print('initialize the network randomly with 0000 bias -----------   hello wangxiaowu! come on!!')
    else:
        print('initialize the network randomly  -----------   hello wangxiaowu! come on!!')
    model.load_state_dict(model_dict)
    # os.mkdir(log_dir)

model = model.cuda()

torch.save(model, os.path.join(log_dir, 'model.pkl'))
print('initial model is save at %s' % log_dir)
print('the margin of ------------ loss function is ----------%f' % args.m)

if args.m == 0.5 and args.nums is None:
    print('-------------use default margin -----------------')
    criterion = losses.create(args.loss).cuda()
elif args.nums is None:
    criterion = losses.create(args.loss, margin=args.m).cuda()
else:
    nums = chars2nums(args.nums)
    print('-------------use nums -----------------' , nums)
    criterion = losses.create(args.loss, nums=nums).cuda()
# fine tune the model: the learning rate for pretrained parameter is 1/10
new_param_ids = set(map(id, model.Embed.parameters()))

new_params = [p for p in model.parameters() if
               id(p) in new_param_ids]

base_params = [p for p in model.parameters() if
              id(p) not in new_param_ids]
param_groups = [
            {'params': base_params, 'lr_mult': args.base},
            {'params': new_params, 'lr_mult': 1.0}]

learn_rate = args.lr
optimizer = optim.Adam(param_groups, lr=learn_rate,
                       weight_decay=args.weight_decay)

data = DataSet.create(args.data, root=None, test=False)
train_loader = torch.utils.data.DataLoader(
    data.train, batch_size=args.BatchSize,
    sampler=RandomIdentitySampler(data.train, num_instances=args.num_instances),
    drop_last=True, num_workers=args.nThreads)


def adjust_learning_rate(opt_, epoch_, num_epochs):
    """Sets the learning rate to the initial LR decayed by 100 at last epochs"""
    if epoch_ > (num_epochs - args.step):
        lr = args.lr*(0.01 ** ((epoch_ + args.step - num_epochs) / float(args.step)))
        for param_group in opt_.param_groups:
            param_group['lr'] = lr

for epoch in range(args.start, args.epochs):
    adjust_learning_rate(optimizer, epoch, args.epochs)
    running_loss = 0.0
    for i, data in enumerate(train_loader, 0):
        # get the inputs
        inputs, labels = data
        # break
        # wrap them in Variable
        inputs = Variable(inputs.cuda())
        labels = Variable(labels).cuda()

        # zero the parameter gradients
        optimizer.zero_grad()

        # forward + backward + optimize
        embed_feat = model(inputs)

        # loss = criterion(embed_feat, labels)
        loss, inter_, dist_ap, dist_an = criterion(embed_feat, labels)
        if args.orth_cof > 1e-9:
            loss = orth_reg(model, loss, cof=args.orth_cof)
        loss.backward()
        optimizer.step()
        running_loss += loss.data[0]
    # print(epoch)
    print('[epoch %05d]\t loss: %.7f \t prec: %.3f \t pos-dist: %.3f \tneg-dist: %.3f'
          % (epoch + 1,  running_loss, inter_, dist_ap, dist_an))
    if epoch % args.save_step == 0:
        torch.save(model, os.path.join(log_dir, '%d_model.pkl' % epoch))

torch.save(model, os.path.join(log_dir, '%d_model.pkl' % epoch))

print('Finished Training')


