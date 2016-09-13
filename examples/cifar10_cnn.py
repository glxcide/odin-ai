from __future__ import print_function, division, absolute_import

import os
os.environ['ODIN'] = 'float32,gpu,theano,seed=12082518'

import numpy as np

from odin import fuel as F, nnet as N, backend as K, training, utils

# ===========================================================================
# Load dataset
# ===========================================================================
ds = F.load_cifar10()
print(ds)
X_learn = ds['X_train']
y_learn = ds['y_train']
X_test = ds['X_test']
y_test = ds['y_test']

# ===========================================================================
# Create network
# ===========================================================================
X_train = K.placeholder(shape=(None,) + X_learn.shape[1:], name='X_train',
                        for_training=True)
X_pred = K.placeholder(shape=(None,) + X_learn.shape[1:], name='X_pred',
                       for_training=False)
y_true = K.placeholder(shape=(None,), name='y_true', dtype='int32')

f = N.Sequence([
    lambda x: K.div(x, 255),
    N.Conv2D(32, (3, 3), pad='same', stride=(1, 1), activation=K.relu),
    N.Conv2D(32, (3, 3), pad='same', stride=(1, 1), activation=K.relu),
    N.Pool2D(pool_size=(2, 2), ignore_border=True, strides=None, mode='max'),

    N.Conv2D(64, (3, 3), pad='same', stride=(1, 1), activation=K.relu),
    N.Conv2D(64, (3, 3), pad='same', stride=(1, 1), activation=K.relu),
    N.Pool2D(pool_size=(2, 2), ignore_border=True, strides=None, mode='max'),

    N.Dropout(level=0.25),

    N.FlattenRight(outdim=2),
    N.Dense(512, activation=K.relu),
    N.Dropout(level=0.5),
    N.Dense(10, activation=K.softmax)
])
y_train = f(X_train)
y_pred = f(X_pred)

cost_train = K.mean(K.categorical_crossentropy(y_train, y_true))
cost_pred = K.mean(K.categorical_accuracy(y_pred, y_true))
cost_eval = K.mean(K.categorical_crossentropy(y_pred, y_true))
parameters = f.parameters

updates = K.optimizers.rmsprop(cost_train, parameters,
                               learning_rate=0.001)

print("Build training function ...")
f_train = K.function([X_train, y_true], cost_train, updates=updates)
print("Build scoring function ...")
f_score = K.function([X_pred, y_true], [cost_pred, cost_eval])

# ===========================================================================
# Create trainer
# ===========================================================================
print("Create trainer ...")
trainer = training.MainLoop(batch_size=128, seed=-1, shuffle_level=0)
trainer.set_save(utils.get_modelpath('cifar10.ai', override=True), f)
trainer.set_task(f_train, [X_learn, y_learn], epoch=25, p=1, name='Train')
trainer.set_subtask(f_score, [X_test, y_test], freq=0.8, name='Valid')
trainer.set_callback([
    training.ProgressMonitor(name='Train', format='Results: %.4f'),
    training.ProgressMonitor(name='Valid', format='Results: %.4f:%.4f'),
    # early stop based on crossentropy on test (not a right procedure,
    # but only for testing)
    training.EarlyStopGeneralizationLoss(name='Valid', threshold=5, patience=1,
                                         get_value=lambda x: np.mean([j for i, j in x])),
    training.History()
])
trainer.run()

# ===========================================================================
# Evaluation and visualization
# ===========================================================================
trainer['History'].print_epoch('Train')
trainer['History'].print_epoch('Valid')