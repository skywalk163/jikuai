from pathlib import Path
import random
import sys
IMG_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.ppm', '.bmp', '.pgm', '.tif',
                  '.tiff', '.webp')

class Dataset(object):
    """A generic data loader where the samples are arranged in this way:

        root/class_a/1.ext
        root/class_a/2.ext
        root/class_a/3.ext

        root/class_b/123.ext
        root/class_b/456.ext
        root/class_b/789.ext"""

    def __init__(self,
                 root,
                 extensions=None,
                 is_valid_file=None):
        self.root = root
        if extensions is None:
            extensions = IMG_EXTENSIONS
        classes, class_to_idx = self._find_classes(self.root)
        # print(classes, class_to_idx )
        # samples = self._find_classes(self.root)
        samples = make_dataset(self.root, class_to_idx, extensions,
                               is_valid_file)
        if len(samples) == 0:
            raise (RuntimeError(
                "Found 0 directories in subfolders of: " + self.root + "\n"
                "Supported extensions are: " + ",".join(extensions)))
        # # print(samples)

        self.samples = samples

    def paddleclastxt(self, eval=None):
        # return self.samples
        trainpath = Path("train.txt")
        evalpath = Path("eval.txt")
        trainlist = None
        evallist = None
        random.shuffle(self.samples)
        # print(self.samples, type(self.samples))
        if eval :
            evalnum = None
            try:
                evalnum = float(eval)
            except:
                pass
            # print(eval,evalnum)
            if eval == "eval" :
                evallist = self.samples
            elif evalnum and 0<evalnum<1 :
                evallistnum = int(len(self.samples) * evalnum)
                trainlist = self.samples[:evallistnum]
                evallist = self.samples[evallistnum:]
            else :
                trainlist = self.samples
        else :
            trainlist = self.samples

        if trainlist:
            with open(trainpath, "w") as f: 
                for i in trainlist:
                    f.writelines(f"{i[0]} {i[1]}\n")    
        if evallist:
            with open(evalpath, "w") as f: 
                for i in evallist:
                    f.writelines(f"{i[0]} {i[1]}\n")
        return "PaddleClas train.txt or eval.txt output OK!"

    def _find_classes(self, dir):
        """
        Finds the class folders in a dataset.

        Args:
            dir (string): Root directory path.

        Returns:
            tuple: (classes, class_to_idx) where classes are relative to (dir), 
                    and class_to_idx is a dictionary.

        """
        classes = [d.name for d in Path(dir).iterdir() if d.is_dir()]
        
        classes.sort()
        class_to_idx = {classes[i]: i for i in range(len(classes))}
        return classes, class_to_idx

def make_dataset(dir, class_to_idx, extensions, is_valid_file=None):
    images = []
    dir = Path(dir)

    if extensions is not None:

        def is_valid_file(x):
            return Path(x).suffix in  extensions

    for target in sorted(class_to_idx.keys()):
        # d = os.path.join(dir, target)
        # if not os.path.isdir(d):
        #     continue
        d = dir/target
        if not d.is_dir:
            continue
        for i in d.iterdir():
            # print(i)
            if i.suffix in extensions:
                item = (str(i), class_to_idx[target])
                images.append(item)
        # for root, _, fnames in sorted(os.walk(d, followlinks=True)):
        #     for fname in sorted(fnames):
        #         path = os.path.join(root, fname)
        #         if is_valid_file(path):
        #             item = (path, class_to_idx[target])
        #             images.append(item)

    return images

if __name__ == "__main__":
    dataset = Dataset(".")
    print(dataset.paddleclastxt(0.8))
#0.08