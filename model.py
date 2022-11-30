import torch
from torch import nn
import torchvision
from torch.nn.utils.weight_norm import weight_norm
import torch.nn.functional as F

if torch.cuda.is_available():
    device = torch.device("cuda")
else:
    device = torch.device("cpu")

# Attention Block
class AttModule(nn.Module):
    """
    Attention Module for Architecture.
    """

    def __init__(self, featureSize, decodeSize, attSize, dropout=0.5):
        """
        featureSize: dimensions of encoded images
        decodeSize: dimension of RNN (decode)
        attSize: dimension provided by attention module
        """
        super(AttModule, self).__init__()

        self.relu = nn.ReLU()

        # process image
        self.att_feat = weight_norm(nn.Linear(featureSize, attSize))  
        
        self.softmax = nn.Softmax(dim=1)

        # process decoded values
        self.att_decoder = weight_norm(nn.Linear(decodeSize, attSize))  
        
        # for softmax
        self.att = weight_norm(nn.Linear(attSize, 1))
        
        self.dropout = nn.Dropout(p=dropout)

    def forward(self, image, h1):
        """
        Forward propagation.
        image: encoded images - SHAPE: (batchSize, 36, features_dim)
        h1: previous decoder output - SHAPE: (batchSize, decoder_dim)
        """

        # Shape: (batch size, attention_dim)
        h1_att = self.att_decoder(h1)

        # Shape: (batch size,36, attention_dim)
        img_att = self.att_feat(image)     
        
        # Shape: (batch size, 36)
        att1 = self.dropout(self.relu(h1_att.unsqueeze(1) + img_att))
        att2 = self.att(att1)
        attention = att2.squeeze(2)  
        
        # Shape: (batch size, 36)
        sigmoid = self.softmax(attention)  
        
        # Shape: (batch size, featureSize)
        aw_images = torch.sum((image * sigmoid.unsqueeze(2)), dim=1) 

        return aw_images, sigmoid


class DecoderAttModule(nn.Module):
    """
    Decoder.
    """

    def __init__(self, attSize, embedSize, decodeSize, vocabSize, featureSize=2048, dropout=0.5):
        """
        embedSize: embedding dimensions
        attSize: dimension - attention network
        vocabSize: vocabulary size
        decodeSize: dimesion of RNN
        featureSize: feature size of encoded images
        dropout: dropout
        """
        super(DecoderAttModule, self).__init__()

        self.attSize = attSize
        self.decodeSize = decodeSize
        self.vocabSize = vocabSize
        self.featureSize = featureSize
        self.embedSize = embedSize
        self.dropout = dropout

        """ Attention Module """
        self.attModule = AttModule(featureSize, decodeSize, attSize) 

        """ Embedding """
        self.embedding = nn.Embedding(vocabSize, embedSize)
        
        # LSTM layer for top-down attention
        self.TD = nn.LSTMCell(embedSize + featureSize + decodeSize, decodeSize, bias=True)
        
        # Dropout
        self.dropout = nn.Dropout(p=self.dropout)
        
        # Language LSTM layer
        self.lang_layer = nn.LSTMCell(featureSize + decodeSize, decodeSize, bias=True)
        
        """ Scoring Layers for Vocabulary """
        self.linear = weight_norm(nn.Linear(decodeSize, vocabSize))  
        
        self.init_weights() 

    def init_weights(self):
        """
        Uniformly distribute the parameter values in initialization
        """
        self.linear.bias.data.fill_(0)
        self.linear.weight.data.uniform_(-0.1, 0.1)
        self.embedding.weight.data.uniform_(-0.1, 0.1)  

    def init_hidden_state(self, batchSize):
        """
        use encoded images to create states for decoding layer - LSTM.
        batchSize: encoded images
        output: hidden states, cell states
        """
        
        hidden_states = torch.zeros(batchSize,self.decodeSize).to(device)
        cell_states = torch.zeros(batchSize,self.decodeSize).to(device)

        return hidden_states, cell_states


    def forward(self, feats, sequences, sizes):
        """
        feats: encoded images - SHAPE: (batchSize, enc_image_size, enc_image_size, encoder_dim)
        sizes: caption lengths - SHAPE: (batchSize, 1)
        sals: salient features - SHAPE: ()
        sequences: encoded captions - SHAPE: (batchSize, max_caption_length)
        OUTPUT: vocab scores, sorted caption sequences, sizes, weights, indices etc.
        """

        vocabSize = self.vocabSize

        featureSize = feats.size(1)*feats.size(1)

        batchSize = feats.size(0)

        # Flatten image
        featsAvg = torch.mean(feats, dim=1).to(device)

        # We sort the data in order to implement the 'timestep' strategy described in the paper (3.2.2)
        sizes, positions = torch.sort((torch.squeeze(sizes, 1)), dim=0, descending=True)
        feats = feats[positions]
        featsAvg = featsAvg[positions]
        sequences = sequences[positions]

        # Embedding
        embeddings = self.embedding(sequences)  # (batchSize, max_size, embedSize)

        # Initialize LSTM state
        hidden1, cell1 = self.init_hidden_state(batchSize)  # (batchSize, decodeSize)
        hidden2, cell2 = self.init_hidden_state(batchSize)  # (batchSize, decodeSize)
        
        decode_lengths = torch.Tensor.tolist((sizes - 1))

        # VOCAB SCORING
        preds = torch.zeros(batchSize, max(decode_lengths), vocabSize).to(device)
        attentions = torch.zeros(batchSize, max(decode_lengths), featureSize).to(device)
        
        """
        1) Hidden States, Mean-Pooled Features, Word Embeddings -> Top-Down Attention Model
        2) Top Down Model, Bottom-Up Features -> Attention Module
        3) Weighed Features, Hidden States -> Language Model
        """
        for timestep in range(max(decode_lengths)):
            bSize = sum([seq_length > timestep for seq_length in decode_lengths])
            
            # 1)
            hidden1,cell1 = self.TD(
                torch.cat([hidden2[:bSize],featsAvg[:bSize],embeddings[:bSize, timestep, :]], dim=1),(hidden1[:bSize], cell1[:bSize]))
            aw_images, sigmoid = self.attModule(feats[:bSize],hidden1[:bSize])
            
            # 2)
            hidden2,cell2 = self.lang_layer(
                torch.cat([aw_images[:bSize],hidden1[:bSize]], dim=1),
                (hidden2[:bSize], cell2[:bSize]))
            
            # 3) - SHAPE: (bSize, vocabSize)
            predictions = self.linear(self.dropout(hidden2))

            preds[:bSize, timestep, :] = predictions
            
        return preds, sequences, decode_lengths, sigmoid, positions






            
