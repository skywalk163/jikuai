# jikuai Package

This is a simple tools package for AI.
0.08


#### 介绍
AI训练小工具
目前的功能是通过把不同目录的分类数据集写成train.txt文件，以便PaddleClas使用。

#### 软件架构
目标是极简实现功能，不用其它任何非python官方自带的库。


#### 安装教程

1.  直接使用pip install jikuai进行安装


#### 使用说明

1.  
想把数据集列表放在哪里，就在哪个目录下执行下面的命令。这里我们生成数据集列表文件在~/ ,以notebook cell单元格格式为例：
```
%cd ~/
from jikuai.dataset import Dataset
dataset = Dataset("work/data/螺栓质量检测-训练集") # 参数为数据集所在的位置，是分类目录的上一级目录
dataset.paddleclasout(0.8) # 生成训练集和测试集列表，参数为两者划分的比例值

```
这样就会在~/目录，生成train.txt和eval.txt两个文件。使用PaddleClas分类训练的时候，在yaml配置文件中设置这两个文件参数即可。

dataset.paddleclasout可跟的参数为：
字符串 "train" ：output train.txt文件
字符串 "eval"  ：output eval.txt文件
数字或字符串数字：会根据该数值划分训练集和验证集，也就是ouput train.txt文件和eval.txt文件。数字和字符串数字的范围在[0, 1]之间，比如0.8或“0.8”表示训练集占比80%，测试集占比20% 。




#### 参与贡献

1.  Fork 本仓库
2.  新建 xxx 分支
3.  提交代码
4.  新建 Pull Request


#### 附录
本项目下一步的计划有两点：
1 增加对PaddleSeg和PaddleDetection等数据集的输出支持
2 增加模糊参数功能，即用户可以用类似“取xxx目录数据集输出成为PaddleClas格式训练文件，8:2划分训练集和验证集”这样的参数达到目的。


