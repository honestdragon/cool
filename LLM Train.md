Excellent question—this gets to the very heart of how these models actually *learn*. I'll walk through the complete training workflow, the mechanics of weight updates, and the precise configuration of the weight matrices.

---

# PART 1: THE TRAINING LOOP — A SINGLE STEP IN EXCRUCIATING DETAIL

Let's trace what happens when the model processes one batch of text during pretraining.

## 1.1 Setup

We have a batch of sequences. For concreteness:
- Batch size `B = 4` sequences
- Sequence length `S = 4096` tokens each
- Vocabulary `V = 50,000`
- Model dimension `d_model = 4096`
- Number of layers `L = 32`
- Feed-forward hidden dim `d_ff = 11008` (for LLaMA-2 7B)

Total tokens in this batch: `B × S = 16,384` tokens.

## 1.2 Forward Pass

### Step 0: Tokenization
Text is tokenized into integer IDs. For our batch:
```
Input:  [[12, 543, 7891, ..., 234],   # Sequence 1
         [456, 32, 890, ..., 12],      # Sequence 2
         ...]
Shape:  (B, S) = (4, 4096)
```

### Step 1: Embedding Lookup
Each token ID looks up its vector in the embedding matrix:
```
W_emb:  (V × d_model) = (50000 × 4096)
```
This is a learned matrix. During training, we index into it:
```
X = W_emb[input_ids]  
Shape: (4, 4096, 4096)  # (B, S, d_model)
```
If the model uses weight tying, this same matrix will be transposed and used at the output.

### Step 2: Position Encoding (RoPE)
We add positional information. For RoPE, we don't add vectors—we rotate the queries and keys in attention (I'll show where this happens below).

### Step 3: Through Each Layer

For layer `l` (there are `L` layers total):

**Pre-norm:**
```
X_norm = RMSNorm(X)
```
RMSNorm computes:
```
RMS(x) = sqrt(mean(x²) + ε)
X_norm = X / RMS(X) * γ
```
Where `γ` is a learnable scale vector of shape `(d_model,)`. Each layer has its own `γ`. Very small parameter count.

**Attention Block:**

For each head `h ∈ {0, 1, ..., 31}` (32 heads, each `d_head = d_model / 32 = 128`):

We have weight matrices:
```
W_Q: (d_model × d_head) = (4096 × 128)  → one per head
W_K: (d_model × d_head) = (4096 × 128)  
W_V: (d_model × d_head) = (4096 × 128)
```
Actually, these are concatenated for efficiency:
```
W_QKV: (d_model × 3·d_model) = (4096 × 12288)
```
One big matrix that projects to Q, K, V for all heads simultaneously.

The computation:
```
QKV = X_norm @ W_QKV    # Shape: (4, 4096, 12288)
```
Then we split into heads:
```
Q, K, V = split(QKV into 3)        # Each: (4, 4096, 4096)
Q = reshape(Q): (4, 32, 4096, 128)  # (B, n_heads, S, d_head)
K = reshape(K): (4, 32, 4096, 128)
V = reshape(V): (4, 32, 4096, 128)
```

Now apply RoPE (Rotary Position Embedding). For position `pos` and dimension pair `(2i, 2i+1)`:
```
θ_i = pos / base^(2i/d_head)   where base = 10000

For Q at position pos:
  Q[pos, 2i]   = Q[pos, 2i] * cos(θ_i) - Q[pos, 2i+1] * sin(θ_i)
  Q[pos, 2i+1] = Q[pos, 2i] * sin(θ_i) + Q[pos, 2i+1] * cos(θ_i)
```
This is applied identically to K. RoPE has no learnable parameters—it's a fixed transformation based on position.

**Attention computation** (per head, per batch item):
```
Scores = Q @ K^T / sqrt(128)     # (4, 32, 4096, 4096)
```

Apply causal mask (upper triangle = -∞):
```
Mask = [[0, -∞, -∞, ...],
        [0,  0, -∞, ...],
        [0,  0,  0, ...]]
Scores_masked = Scores + Mask
```

Softmax and apply to values:
```
Attn_weights = softmax(Scores_masked, dim=-1)  # (4, 32, 4096, 4096)
Attn_output = Attn_weights @ V                  # (4, 32, 4096, 128)
```

Merge heads back:
```
Attn_concat = reshape: (4, 4096, 4096)  # (B, S, d_model)
```

Final projection:
```
W_O: (d_model × d_model) = (4096 × 4096)
Output_attn = Attn_concat @ W_O   # (4, 4096, 4096)
```

Add residual:
```
X = X + Output_attn    # (4, 4096, 4096)
```

**FFN Block:**

Pre-norm again:
```
X_norm2 = RMSNorm(X)
```

SwiGLU FFN. Three weight matrices:
```
W_gate: (4096 × 11008)    # Gate projection
W_up:   (4096 × 11008)    # Up projection  
W_down: (11008 × 4096)    # Down projection
```

Computation:
```
gate = X_norm2 @ W_gate          # (4, 4096, 11008)
up   = X_norm2 @ W_up            # (4, 4096, 11008)

activated = SiLU(gate) * up      # SiLU(x) = x * σ(x)
                                  # Element-wise multiplication

output = activated @ W_down     # (4, 4096, 4096)
```

Add residual:
```
X = X + output
```

This repeats for all `L` layers.

### Step 4: Final Projection

Final norm:
```
X_final = RMSNorm(X)     # (4, 4096, 4096)
```

LM Head (output projection):
```
W_lm: (d_model × V) = (4096 × 50000)
logits = X_final @ W_lm   # (4, 4096, 50000)
```

If weight tying is used, `W_lm = W_emb^T`.

### Step 5: Loss Computation

For each position, we have logits over vocabulary and the actual next token.

Cross-entropy with label smoothing (optional):
```
For each token at position i in sequence j:
  target = input_ids[j, i+1]   # the actual next token
  
  loss_token = -log(softmax(logits[j, i, :])[target])
  
  If label smoothing with ε=0.1:
    loss_token = -(1-ε)*log(p_correct) - ε*mean(log(p_all))
```

Total loss:
```
L = mean(loss_token over all positions)
```

This single scalar `L` is what we'll backpropagate.

---

# PART 2: BACKWARD PASS — HOW GRADIENTS FLOW

## 2.1 The Chain Rule Journey

The loss `L` is a scalar. We need `∂L/∂W` for every weight matrix.

The backward pass traverses the computation graph in reverse.

### Starting: Gradient of Loss w.r.t Logits

For a single token position with correct class `c`:
```
∂L/∂logit_c = softmax(logits)[c] - 1
∂L/∂logit_i = softmax(logits)[i]      for i ≠ c
```
This is the beautiful property of cross-entropy + softmax: the gradient is simply `(predicted_prob - target_one_hot)`.

Shape: `(4, 4096, 50000)`.

### LM Head Weights

```
logits = X_final @ W_lm
```

By chain rule:
```
∂L/∂W_lm = X_final^T @ (∂L/∂logits)
```
Shape: `(4096 × 50000) = (4096 × 4*4096) @ (4*4096 × 50000)`

We accumulate this across the batch dimension and all sequence positions.

```
∂L/∂X_final = (∂L/∂logits) @ W_lm^T
```
Shape: `(4, 4096, 4096)`

### Through RMSNorm

RMSNorm is:
```
y = x / sqrt(mean(x²) + ε) * γ
```

The gradient is:
```
∂L/∂x = (γ / RMS(x)) * (∂L/∂y - (x * mean(∂L/∂y * x)) / RMS(x)²)
∂L/∂γ = sum(∂L/∂y * x / RMS(x), over non-batch dims)
```

### Through Residual Connections

Since `X_l+1 = X_l + F(X_l)`, the gradient splits:
```
∂L/∂X_l = ∂L/∂X_l+1 + ∂L/∂F(X_l) * ∂F/∂X_l
```
The gradient flows through *both* paths—through the residual and through the function. This is why residuals prevent vanishing gradients.

### Through Attention

The backward pass through attention is non-trivial. Let's trace it.

We have `Attn_output = softmax(Q@K^T/√d) @ V`.

Let `S = Q@K^T/√d` and `A = softmax(S)` and `O = A@V`.

Given `∂L/∂O`, we need gradients for Q, K, V.

```
∂L/∂A = ∂L/∂O @ V^T
∂L/∂S = A * (∂L/∂A - sum(∂L/∂A * A, dim=-1))   # softmax gradient
∂L/∂Q = (1/√d) * ∂L/∂S @ K
∂L/∂K = (1/√d) * ∂L/∂S^T @ Q
∂L/∂V = A^T @ ∂L/∂O
```

Then these flow back through the projection matrices:
```
∂L/∂W_Q = X_norm^T @ ∂L/∂Q
∂L/∂W_K = X_norm^T @ ∂L/∂K
∂L/∂W_V = X_norm^T @ ∂L/∂V
∂L/∂W_O = Attn_concat^T @ ∂L/∂O  (before residual)
```

### Through SwiGLU FFN

```
gate = X @ W_gate
up = X @ W_up
h = SiLU(gate) * up
output = h @ W_down
```

Given `∂L/∂output`:
```
∂L/∂W_down = h^T @ ∂L/∂output
∂L/∂h = ∂L/∂output @ W_down^T

∂L/∂gate = ∂L/∂h * up * SiLU'(gate)
  where SiLU'(x) = σ(x) + x*σ(x)*(1-σ(x))
∂L/∂up = ∂L/∂h * SiLU(gate)

∂L/∂W_gate = X^T @ ∂L/∂gate
∂L/∂W_up = X^T @ ∂L/∂up
∂L/∂X = ∂L/∂gate @ W_gate^T + ∂L/∂up @ W_up^T
```

### Through Embeddings

The embedding lookup is just indexing. The gradient `∂L/∂W_emb` accumulates the gradients flowing to each embedded position into the rows corresponding to the token IDs that were looked up. Positions with the same token ID sum their gradients.

---

## 2.2 The Scale of One Backward Pass

For our batch of 16,384 tokens:

**Activations stored in memory** (for computing gradients):
- At each of 32 layers: X_norm, Q, K, V, attention weights, gate, up, down projections
- Total: roughly `2 × params × sequences × precision_bytes`
- For 7B model, FP16: ~56 GB just for activations
- This is why gradient checkpointing is used: recompute activations during backward instead of storing, trading ~30% more compute for ~4x less memory

---

# PART 3: WEIGHT UPDATES — THE OPTIMIZER

## 3.1 Gradient Accumulation

One "step" of the optimizer doesn't happen after every micro-batch. We accumulate gradients:

```
Global batch = 4M tokens
Micro-batch = 16,384 tokens (what fits in GPU memory)
Accumulation steps = 4M / 16,384 ≈ 244
```

Gradients are summed across all micro-batches (and across all GPUs via all-reduce) before the optimizer step.

## 3.2 AdamW Update Rule

For each weight matrix, we maintain:
- `m`: exponential moving average of gradients (momentum)
- `v`: exponential moving average of squared gradients (velocity)

For step `t`:

### Step 1: Get total gradient
```
g_t = mean(∂L/∂W over global batch) + λ * W_t    # λ is weight decay
```
The `mean` is because we average loss over tokens, not sum. The `+ λ*W_t` adds weight decay regularization.

### Step 2: Update moment estimates
```
m_t = β₁ * m_{t-1} + (1 - β₁) * g_t
v_t = β₂ * v_{t-1} + (1 - β₂) * g_t²
```
Typical values: `β₁ = 0.9`, `β₂ = 0.95` or `0.999`

### Step 3: Bias correction
```
m̂_t = m_t / (1 - β₁^t)
v̂_t = v_t / (1 - β₂^t)
```
This corrects for the zero-initialization of m and v.

### Step 4: Weight update
```
W_{t+1} = W_t - η * m̂_t / (√v̂_t + ε)
```
Where:
- `η`: learning rate (e.g., 3e-4 initially, decaying)
- `ε`: small constant for numerical stability (1e-8)

## 3.3 Learning Rate Schedule

A typical pretraining schedule:

```
1. Linear warmup: η increases from 0 to η_max over ~2000 steps
2. Cosine decay: η = η_min + 0.5*(η_max - η_min)*(1 + cos(π * step/total_steps))
   or constant with decay at end
```

Example values for 7B model:
- `η_max = 3e-4`
- `η_min = 3e-5` (10% of max)
- Warmup steps: 2000
- Total steps: ~250,000 (for 1T tokens with 4M batch size)

---

# PART 4: THE WEIGHT MATRICES — PRECISE CONFIGURATION

Let's enumerate every single parameter tensor in a LLaMA-2 7B model.

## 4.1 Token Embedding
```
W_emb: (32000 × 4096) = 131,072,000 parameters
```
(LLaMA-2 uses V=32000 vocabulary)

## 4.2 Per Layer (×32 layers)

**Attention:**
```
W_Q: (4096 × 4096) = 16,777,216
W_K: (4096 × 1024) =  4,194,304   (GQA: 8 KV heads × 128 = 1024)
W_V: (4096 × 1024) =  4,194,304
W_O: (4096 × 4096) = 16,777,216
```
Total attention per layer: 41,943,040

**FFN (SwiGLU):**
```
W_gate: (4096 × 11008) = 45,088,768
W_up:   (4096 × 11008) = 45,088,768
W_down: (11008 × 4096) = 45,088,768
```
Total FFN per layer: 135,266,304

**RMSNorm:**
```
γ_attn: (4096,) = 4096
γ_ffn:  (4096,) = 4096
```
(negligible, but every parameter counts)

**Total per layer:** `41,943,040 + 135,266,304 + 8,192 = 177,217,536`

## 4.3 Final Layers
```
Final RMSNorm γ: (4096,)
LM Head (if not tied): (4096 × 32000) = 131,072,000
```

## 4.4 Total Count

```
Embedding:        131,072,000
32 × Layers:    5,670,961,152
Final Norm:            4,096
LM Head:          131,072,000 (if untied)
-----------------------------------
Total:         ~6,738,109,248  ≈ 6.74B parameters
```

Wait, that's not 7B. The difference: LLaMA-2 7B actually uses `d_model=4096`, `d_ff=11008`, `n_layers=32`, but also `n_heads=32`, `n_kv_heads=32` in the 7B version (no GQA). Let me correct:

For LLaMA-2 7B (no GQA, all 32 heads have full K,V):
```
W_Q: (4096 × 4096)
W_K: (4096 × 4096)   # Full, not GQA
W_V: (4096 × 4096)
W_O: (4096 × 4096)
```
That's `4 × 16,777,216 = 67,108,864` for attention per layer.

```
FFN: 3 × (4096 × 11008) = 135,266,304
```

```
Per layer: 67,108,864 + 135,266,304 = 202,375,168
32 layers: 6,476,005,376
Embedding:   131,072,000
LM Head:     131,072,000 (if untied)
Final norm:       4,096
-----------------------------------
Total:     6,738,153,472 ≈ 6.74B
```

It's called "7B" because they round up slightly, or include embedding parameters differently in the count.

---

# PART 5: PRACTICAL TRAINING DYNAMICS

## 5.1 What Happens During Training

**Early phase (first ~1000 steps):**
- Embeddings organize into semantically meaningful clusters
- Attention initially learns positional patterns (nearby tokens)
- Loss drops fast

**Mid phase (majority of training):**
- Attention heads specialize (induction heads form)
- FFN layers build knowledge associations
- Loss decreases slowly, linearly with log(compute)
- In-context learning emerges

**Late phase (near end):**
- Model refines boundaries between concepts
- Performance on reasoning tasks improves
- Grokking can occur on structured tasks

## 5.2 Memory Breakdown During Training (7B model, FP16)

```
Model weights:           14 GB  (7B × 2 bytes)
Optimizer states:        56 GB  (Adam: m + v = 2× weights, FP32 = 4×)
                                          Actually: 7B × 4 bytes × 3 = 84 GB?
                                          Wait: params FP16=14GB, 
                                          optimizer m(FP32)=28GB, v(FP32)=28GB
                                          Total: 14 + 28 + 28 = 70 GB
Gradients (FP16):        14 GB
Activations:             56 GB  (with gradient checkpointing: ~14 GB)
-----------------------------------
Total per GPU:          ~112 GB (needs multiple GPUs or ZeRO sharding)
```

## 5.3 Gradient Flow Health

A healthy training run shows:
- Gradient norm ≈ 0.1-1.0 (after clipping)
- No layer has vanishing gradients (all norms similar)
- Attention and FFN gradients balanced
- No NaN values (this kills training immediately)

Gradient clipping prevents:
```
if ||g|| > max_norm:
    g = g * max_norm / ||g||
```

## 5.4 What the Weights "Look" Like

- Embeddings develop clear structure (can do analogies via vector arithmetic)
- Attention W_Q, W_K develop circular patterns (from RoPE)
- W_O often shows low-rank structure
- FFN weights become sparse in activation space
- Layer norms converge quickly and change little

---

# PART 6: DISTRIBUTED TRAINING IMPACT ON GRADIENTS

When using multiple GPUs:

**Data Parallel (DP):**
- Each GPU has full model copy
- Different micro-batch
- Gradients computed independently
- All-reduce sums gradients: `g_total = Σ_gpu g_gpu`
- Then identical optimizer step on each GPU

**Tensor Parallel (TP):**
- Weight matrix split across GPUs
- Forward: communicate intermediate results
- Backward: communicate gradients
- Each GPU only has a shard of the weight, shard of the gradient
- Optimizer step is local on each shard

**ZeRO Stage 2:**
- Parameters duplicated on all GPUs
- Gradients all-reduced (averaged)
- Optimizer states sharded: each GPU holds 1/N of m and v
- After optimizer step: all-gather updated weights

---

## Summary: The Complete Flow

```
FORWARD PASS:
Raw text → token IDs → embeddings + RoPE → 
[32×: norm → attention(QKV projection, scores, softmax, value weighting) 
          → norm → FFN(gate, up, SiLU multiply, down projection)] → 
final norm → LM head → logits → softmax → cross-entropy loss

BACKWARD PASS:
Loss gradient → LM head gradients → 
[32× in reverse: FFN gradients → norm gradients → 
                 attention gradients (V, then scores, then Q/K) → norm gradients] →
embedding gradients → 
accumulate ∂L/∂W for every parameter matrix

OPTIMIZER STEP (AdamW, after gradient accumulation across micro-batches):
For each weight matrix W:
  g = mean(gradient) + λ*W
  m = β₁*m + (1-β₁)*g
  v = β₂*v + (1-β₂)*g²  
  m̂ = m/(1-β₁ᵗ), v̂ = v/(1-β₂ᵗ)
  W = W - η * m̂/(√v̂ + ε)
```

This process runs trillions of times across thousands of GPUs over months, gradually shaping the weights from random initialization into a compressed representation of language and knowledge.

Would you like me to go deeper into any specific aspect? I could explore:
- The mathematics of RoPE in more detail
- Exactly how gradient checkpointing trades compute for memory
- How mixed precision (FP16/FP32) works in practice
- A concrete numerical example of a single attention head's forward and backward pass



# -----------------------------------------------

I'll build this from scratch with small, concrete numbers you can actually track by hand (or in a spreadsheet). This will demystify exactly what happens inside one attention head.

---

# SETUP: A TINY TOY MODEL

Let's create the smallest meaningful example:

**Sequence:** "The cat sat"
- Tokenized as 3 tokens: `[t₁="The", t₂="cat", t₃="sat"]`

**Model dimensions (tiny):**
- `d_model = 6` (embedding dimension)
- `d_head = 3` (per-head dimension, just 1 head for simplicity)

So each token is a 6-dimensional vector, and attention will project to 3 dimensions for Q, K, V.

---

## 0. Input Embeddings (after all previous layers)

Let's say the input to this attention layer is already processed through embeddings and previous layers. We have:

```
X = [x₁]    [ 0.2  0.5 -0.3  0.1  0.8 -0.2]   (token "The")
    [x₂] =  [-0.1  0.3  0.6 -0.4  0.2  0.7]   (token "cat")
    [x₃]    [ 0.4 -0.2  0.1  0.9 -0.5  0.3]   (token "sat")
```

Shape: `(3, 6)` — 3 tokens, each 6-dimensional.

For this example, we'll compute the attention output for **just the 3rd position** (token "sat"), since that's what happens during autoregressive generation—each token attends to itself and all previous tokens.

---

# PART 1: THE WEIGHT MATRICES

We have learned weight matrices (I'm making up small numbers):

### Q projection matrix (6 → 3)
```
W_Q = [[ 0.1,  0.2, -0.1],    # row 0
       [-0.1,  0.3,  0.2],    # row 1
       [ 0.2, -0.1,  0.1],    # row 2
       [ 0.3,  0.1, -0.2],    # row 3
       [-0.2,  0.2,  0.3],    # row 4
       [ 0.1, -0.3,  0.1]]    # row 5
```
Shape: `(6, 3)`

### K projection matrix (6 → 3)
```
W_K = [[ 0.2, -0.1,  0.3],
       [ 0.1,  0.2, -0.1],
       [-0.1,  0.3,  0.2],
       [ 0.2, -0.2,  0.1],
       [ 0.3,  0.1, -0.1],
       [-0.1,  0.2,  0.3]]
```
Shape: `(6, 3)`

### V projection matrix (6 → 3)
```
W_V = [[ 0.1,  0.3, -0.2],
       [-0.2,  0.1,  0.2],
       [ 0.3, -0.1,  0.1],
       [ 0.1,  0.2,  0.3],
       [-0.1,  0.2, -0.1],
       [ 0.2, -0.3,  0.1]]
```
Shape: `(6, 3)`

### Output projection matrix (3 → 6)
```
W_O = [[ 0.2, -0.1,  0.3,  0.1, -0.2,  0.1],
       [-0.1,  0.2,  0.1, -0.3,  0.2,  0.1],
       [ 0.3,  0.1, -0.2,  0.2,  0.1, -0.1]]
```
Shape: `(3, 6)` — projects back to model dimension

---

# PART 2: FORWARD PASS (Position 3 attending to positions 1,2,3)

## Step 1: Compute Q, K, V for ALL positions

### Compute Q = X @ W_Q

**For x₁ `[0.2, 0.5, -0.3, 0.1, 0.8, -0.2]`:**

```
q₁₀ = 0.2(0.1) + 0.5(-0.1) + (-0.3)(0.2) + 0.1(0.3) + 0.8(-0.2) + (-0.2)(0.1)
    = 0.02 + (-0.05) + (-0.06) + 0.03 + (-0.16) + (-0.02)
    = -0.24

q₁₁ = 0.2(0.2) + 0.5(0.3) + (-0.3)(-0.1) + 0.1(0.1) + 0.8(0.2) + (-0.2)(-0.3)
    = 0.04 + 0.15 + 0.03 + 0.01 + 0.16 + 0.06
    = 0.45

q₁₂ = 0.2(-0.1) + 0.5(0.2) + (-0.3)(0.1) + 0.1(-0.2) + 0.8(0.3) + (-0.2)(0.1)
    = -0.02 + 0.10 + (-0.03) + (-0.02) + 0.24 + (-0.02)
    = 0.25
```

So `q₁ = [-0.24, 0.45, 0.25]`

**For x₂ `[-0.1, 0.3, 0.6, -0.4, 0.2, 0.7]`:**

```
q₂₀ = (-0.1)(0.1) + 0.3(-0.1) + 0.6(0.2) + (-0.4)(0.3) + 0.2(-0.2) + 0.7(0.1)
    = -0.01 + (-0.03) + 0.12 + (-0.12) + (-0.04) + 0.07
    = -0.01

q₂₁ = (-0.1)(0.2) + 0.3(0.3) + 0.6(-0.1) + (-0.4)(0.1) + 0.2(0.2) + 0.7(-0.3)
    = -0.02 + 0.09 + (-0.06) + (-0.04) + 0.04 + (-0.21)
    = -0.20

q₂₂ = (-0.1)(-0.1) + 0.3(0.2) + 0.6(0.1) + (-0.4)(-0.2) + 0.2(0.3) + 0.7(0.1)
    = 0.01 + 0.06 + 0.06 + 0.08 + 0.06 + 0.07
    = 0.34
```

So `q₂ = [-0.01, -0.20, 0.34]`

**For x₃ `[0.4, -0.2, 0.1, 0.9, -0.5, 0.3]`:**

```
q₃₀ = 0.4(0.1) + (-0.2)(-0.1) + 0.1(0.2) + 0.9(0.3) + (-0.5)(-0.2) + 0.3(0.1)
    = 0.04 + 0.02 + 0.02 + 0.27 + 0.10 + 0.03
    = 0.48

q₃₁ = 0.4(0.2) + (-0.2)(0.3) + 0.1(-0.1) + 0.9(0.1) + (-0.5)(0.2) + 0.3(-0.3)
    = 0.08 + (-0.06) + (-0.01) + 0.09 + (-0.10) + (-0.09)
    = -0.09

q₃₂ = 0.4(-0.1) + (-0.2)(0.2) + 0.1(0.1) + 0.9(-0.2) + (-0.5)(0.3) + 0.3(0.1)
    = -0.04 + (-0.04) + 0.01 + (-0.18) + (-0.15) + 0.03
    = -0.37
```

So `q₃ = [0.48, -0.09, -0.37]`

```
Q = [[-0.24,  0.45,  0.25],    # "The"
     [-0.01, -0.20,  0.34],    # "cat"
     [ 0.48, -0.09, -0.37]]    # "sat"
```

### Compute K = X @ W_K

I'll compute these faster:

**x₁ `[0.2, 0.5, -0.3, 0.1, 0.8, -0.2]` @ W_K:**
```
k₁₀ = 0.2(0.2) + 0.5(0.1) + (-0.3)(-0.1) + 0.1(0.2) + 0.8(0.3) + (-0.2)(-0.1)
    = 0.04 + 0.05 + 0.03 + 0.02 + 0.24 + 0.02 = 0.40

k₁₁ = 0.2(-0.1) + 0.5(0.2) + (-0.3)(0.3) + 0.1(-0.2) + 0.8(0.1) + (-0.2)(0.2)
    = -0.02 + 0.10 + (-0.09) + (-0.02) + 0.08 + (-0.04) = 0.01

k₁₂ = 0.2(0.3) + 0.5(-0.1) + (-0.3)(0.2) + 0.1(0.1) + 0.8(-0.1) + (-0.2)(0.3)
    = 0.06 + (-0.05) + (-0.06) + 0.01 + (-0.08) + (-0.06) = -0.18
```
`k₁ = [0.40, 0.01, -0.18]`

**x₂ `[-0.1, 0.3, 0.6, -0.4, 0.2, 0.7]` @ W_K:**
```
k₂₀ = -0.1(0.2) + 0.3(0.1) + 0.6(-0.1) + (-0.4)(0.2) + 0.2(0.3) + 0.7(-0.1)
    = -0.02 + 0.03 + (-0.06) + (-0.08) + 0.06 + (-0.07) = -0.14

k₂₁ = -0.1(-0.1) + 0.3(0.2) + 0.6(0.3) + (-0.4)(-0.2) + 0.2(0.1) + 0.7(0.2)
    = 0.01 + 0.06 + 0.18 + 0.08 + 0.02 + 0.14 = 0.49

k₂₂ = -0.1(0.3) + 0.3(-0.1) + 0.6(0.2) + (-0.4)(0.1) + 0.2(-0.1) + 0.7(0.3)
    = -0.03 + (-0.03) + 0.12 + (-0.04) + (-0.02) + 0.21 = 0.21
```
`k₂ = [-0.14, 0.49, 0.21]`

**x₃ `[0.4, -0.2, 0.1, 0.9, -0.5, 0.3]` @ W_K:**
```
k₃₀ = 0.4(0.2) + (-0.2)(0.1) + 0.1(-0.1) + 0.9(0.2) + (-0.5)(0.3) + 0.3(-0.1)
    = 0.08 + (-0.02) + (-0.01) + 0.18 + (-0.15) + (-0.03) = 0.05

k₃₁ = 0.4(-0.1) + (-0.2)(0.2) + 0.1(0.3) + 0.9(-0.2) + (-0.5)(0.1) + 0.3(0.2)
    = -0.04 + (-0.04) + 0.03 + (-0.18) + (-0.05) + 0.06 = -0.22

k₃₂ = 0.4(0.3) + (-0.2)(-0.1) + 0.1(0.2) + 0.9(0.1) + (-0.5)(-0.1) + 0.3(0.3)
    = 0.12 + 0.02 + 0.02 + 0.09 + 0.05 + 0.09 = 0.39
```
`k₃ = [0.05, -0.22, 0.39]`

```
K = [[ 0.40,  0.01, -0.18],    # "The"
     [-0.14,  0.49,  0.21],    # "cat"
     [ 0.05, -0.22,  0.39]]    # "sat"
```

### Compute V = X @ W_V

**x₁ @ W_V:**
```
v₁₀ = 0.2(0.1) + 0.5(-0.2) + (-0.3)(0.3) + 0.1(0.1) + 0.8(-0.1) + (-0.2)(0.2)
    = 0.02 + (-0.10) + (-0.09) + 0.01 + (-0.08) + (-0.04) = -0.28

v₁₁ = 0.2(0.3) + 0.5(0.1) + (-0.3)(-0.1) + 0.1(0.2) + 0.8(0.2) + (-0.2)(-0.3)
    = 0.06 + 0.05 + 0.03 + 0.02 + 0.16 + 0.06 = 0.38

v₁₂ = 0.2(-0.2) + 0.5(0.2) + (-0.3)(0.1) + 0.1(0.3) + 0.8(-0.1) + (-0.2)(0.1)
    = -0.04 + 0.10 + (-0.03) + 0.03 + (-0.08) + (-0.02) = -0.04
```
`v₁ = [-0.28, 0.38, -0.04]`

**x₂ @ W_V:**
```
v₂₀ = -0.1(0.1) + 0.3(-0.2) + 0.6(0.3) + (-0.4)(0.1) + 0.2(-0.1) + 0.7(0.2)
    = -0.01 + (-0.06) + 0.18 + (-0.04) + (-0.02) + 0.14 = 0.19

v₂₁ = -0.1(0.3) + 0.3(0.1) + 0.6(-0.1) + (-0.4)(0.2) + 0.2(0.2) + 0.7(-0.3)
    = -0.03 + 0.03 + (-0.06) + (-0.08) + 0.04 + (-0.21) = -0.31

v₂₂ = -0.1(-0.2) + 0.3(0.2) + 0.6(0.1) + (-0.4)(0.3) + 0.2(-0.1) + 0.7(0.1)
    = 0.02 + 0.06 + 0.06 + (-0.12) + (-0.02) + 0.07 = 0.07
```
`v₂ = [0.19, -0.31, 0.07]`

**x₃ @ W_V:**
```
v₃₀ = 0.4(0.1) + (-0.2)(-0.2) + 0.1(0.3) + 0.9(0.1) + (-0.5)(-0.1) + 0.3(0.2)
    = 0.04 + 0.04 + 0.03 + 0.09 + 0.05 + 0.06 = 0.31

v₃₁ = 0.4(0.3) + (-0.2)(0.1) + 0.1(-0.1) + 0.9(0.2) + (-0.5)(0.2) + 0.3(-0.3)
    = 0.12 + (-0.02) + (-0.01) + 0.18 + (-0.10) + (-0.09) = 0.08

v₃₂ = 0.4(-0.2) + (-0.2)(0.2) + 0.1(0.1) + 0.9(0.3) + (-0.5)(-0.1) + 0.3(0.1)
    = -0.08 + (-0.04) + 0.01 + 0.27 + 0.05 + 0.03 = 0.24
```
`v₃ = [0.31, 0.08, 0.24]`

```
V = [[-0.28,  0.38, -0.04],    # "The"
     [ 0.19, -0.31,  0.07],    # "cat"
     [ 0.31,  0.08,  0.24]]    # "sat"
```

---

## Step 2: Compute Attention Scores for Position 3

We're computing attention for token "sat" (position 3). It attends to positions 1, 2, and 3.

The query is `q₃ = [0.48, -0.09, -0.37]`

### Score with position 1 (k₁):
```
score₃₁ = q₃ · k₁ = 0.48(0.40) + (-0.09)(0.01) + (-0.37)(-0.18)
        = 0.192 + (-0.0009) + 0.0666
        = 0.2577
```

### Score with position 2 (k₂):
```
score₃₂ = q₃ · k₂ = 0.48(-0.14) + (-0.09)(0.49) + (-0.37)(0.21)
        = -0.0672 + (-0.0441) + (-0.0777)
        = -0.1890
```

### Score with position 3 (k₃):
```
score₃₃ = q₃ · k₃ = 0.48(0.05) + (-0.09)(-0.22) + (-0.37)(0.39)
        = 0.024 + 0.0198 + (-0.1443)
        = -0.1005
```

### Scale by √d_head:
`√d_head = √3 ≈ 1.732`

```
scaled_scores = [0.2577/1.732, -0.1890/1.732, -0.1005/1.732]
              = [0.1488, -0.1091, -0.0580]
```

---

## Step 3: Apply Softmax

```
softmax = exp(s_i) / Σⱼ exp(s_j)

exp(0.1488) = 1.1604
exp(-0.1091) = 0.8966
exp(-0.0580) = 0.9437

sum = 1.1604 + 0.8966 + 0.9437 = 3.0007

α₁ = 1.1604 / 3.0007 = 0.3867   (attention to "The")
α₂ = 0.8966 / 3.0007 = 0.2988   (attention to "cat")
α₃ = 0.9437 / 3.0007 = 0.3145   (attention to "sat")
```

Check: `0.3867 + 0.2988 + 0.3145 = 1.0000` ✓

---

## Step 4: Compute Weighted Sum of Values

```
output_attn = α₁·v₁ + α₂·v₂ + α₃·v₃
```

Component-wise:

**Dimension 0:**
```
0.3867(-0.28) + 0.2988(0.19) + 0.3145(0.31)
= -0.1083 + 0.0568 + 0.0975
= 0.0460
```

**Dimension 1:**
```
0.3867(0.38) + 0.2988(-0.31) + 0.3145(0.08)
= 0.1469 + (-0.0926) + 0.0252
= 0.0795
```

**Dimension 2:**
```
0.3867(-0.04) + 0.2988(0.07) + 0.3145(0.24)
= -0.0155 + 0.0209 + 0.0755
= 0.0809
```

```
output_attn = [0.0460, 0.0795, 0.0809]    # (3,)
```

---

## Step 5: Output Projection

```
output = output_attn @ W_O
```

Recall `W_O` is `(3, 6)`:
```
[[ 0.2, -0.1,  0.3,  0.1, -0.2,  0.1],
 [-0.1,  0.2,  0.1, -0.3,  0.2,  0.1],
 [ 0.3,  0.1, -0.2,  0.2,  0.1, -0.1]]
```

```
o₀ = 0.0460(0.2) + 0.0795(-0.1) + 0.0809(0.3)
   = 0.0092 + (-0.0080) + 0.0243
   = 0.0255

o₁ = 0.0460(-0.1) + 0.0795(0.2) + 0.0809(0.1)
   = -0.0046 + 0.0159 + 0.0081
   = 0.0194

o₂ = 0.0460(0.3) + 0.0795(0.1) + 0.0809(-0.2)
   = 0.0138 + 0.0080 + (-0.0162)
   = 0.0056

o₃ = 0.0460(0.1) + 0.0795(-0.3) + 0.0809(0.2)
   = 0.0046 + (-0.0239) + 0.0162
   = -0.0031

o₄ = 0.0460(-0.2) + 0.0795(0.2) + 0.0809(0.1)
   = -0.0092 + 0.0159 + 0.0081
   = 0.0148

o₅ = 0.0460(0.1) + 0.0795(0.1) + 0.0809(-0.1)
   = 0.0046 + 0.0080 + (-0.0081)
   = 0.0045
```

```
output = [0.0255, 0.0194, 0.0056, -0.0031, 0.0148, 0.0045]
```

This gets added to the residual stream: `x₃_new = x₃ + output`

---

**FORWARD PASS COMPLETE.** Now let's do the backward pass.

---

# PART 3: BACKWARD PASS

Assume we've done the full forward pass (through all layers, loss computation), and gradients have flowed backward to this layer. For position 3, we've received `∂L/∂output` — the gradient of the loss with respect to this attention output.

Let's say:
```
∂L/∂output = [0.1, -0.2, 0.05, 0.15, -0.1, 0.0]    # (6,)
```
This is `dO` — how much changing each dimension of the attention output would affect the loss.

---

## Backward Step 1: Gradient through W_O

We had: `output = output_attn @ W_O` where `output_attn = [0.0460, 0.0795, 0.0809]`

### Gradient w.r.t W_O:
```
∂L/∂W_O = output_attn^T ⊗ dO
```
This is an outer product: `(3,) × (6,) → (3, 6)`

```
∂L/∂W_O = [[0.0460×0.1, 0.0460×(-0.2), 0.0460×0.05, 0.0460×0.15, 0.0460×(-0.1), 0.0460×0.0],
           [0.0795×0.1, 0.0795×(-0.2), 0.0795×0.05, 0.0795×0.15, 0.0795×(-0.1), 0.0795×0.0],
           [0.0809×0.1, 0.0809×(-0.2), 0.0809×0.05, 0.0809×0.15, 0.0809×(-0.1), 0.0809×0.0]]
```

```
∂L/∂W_O = [[ 0.0046, -0.0092,  0.0023,  0.0069, -0.0046,  0.0000],
           [ 0.0080, -0.0159,  0.0040,  0.0119, -0.0080,  0.0000],
           [ 0.0081, -0.0162,  0.0040,  0.0121, -0.0081,  0.0000]]
```

### Gradient w.r.t output_attn (before W_O):
```
d_attn = dO @ W_O^T
```
`dO`: `(6,)`, `W_O^T`: `(6, 3)` → result: `(3,)`

```
W_O^T = [[ 0.2, -0.1,  0.3],
         [-0.1,  0.2,  0.1],
         [ 0.3,  0.1, -0.2],
         [ 0.1, -0.3,  0.2],
         [-0.2,  0.2,  0.1],
         [ 0.1,  0.1, -0.1]]
```

```
d_attn₀ = 0.1(0.2) + (-0.2)(-0.1) + 0.05(0.3) + 0.15(0.1) + (-0.1)(-0.2) + 0.0(0.1)
        = 0.02 + 0.02 + 0.015 + 0.015 + 0.02 + 0.0
        = 0.090

d_attn₁ = 0.1(-0.1) + (-0.2)(0.2) + 0.05(0.1) + 0.15(-0.3) + (-0.1)(0.2) + 0.0(0.1)
        = -0.01 + (-0.04) + 0.005 + (-0.045) + (-0.02) + 0.0
        = -0.110

d_attn₂ = 0.1(0.3) + (-0.2)(0.1) + 0.05(-0.2) + 0.15(0.2) + (-0.1)(0.1) + 0.0(-0.1)
        = 0.03 + (-0.02) + (-0.01) + 0.03 + (-0.01) + 0.0
        = 0.020
```

```
d_attn = [0.090, -0.110, 0.020]    # ∂L/∂(attention_output before W_O)
```

---

## Backward Step 2: Gradient through the Value Weighting

We had: `output_attn = Σⱼ αⱼ · vⱼ` for j = 1,2,3

Given `d_attn`, we need `∂L/∂αⱼ` and `∂L/∂vⱼ`.

### Gradient w.r.t attention weights α:

For each value vector dimension d, the contribution of αⱼ is `vⱼ[d]`:
```
∂L/∂αⱼ = d_attn · vⱼ
```

```
∂L/∂α₁ = d_attn · v₁ = 0.090(-0.28) + (-0.110)(0.38) + 0.020(-0.04)
        = -0.0252 + (-0.0418) + (-0.0008)
        = -0.0678

∂L/∂α₂ = d_attn · v₂ = 0.090(0.19) + (-0.110)(-0.31) + 0.020(0.07)
        = 0.0171 + 0.0341 + 0.0014
        = 0.0526

∂L/∂α₃ = d_attn · v₃ = 0.090(0.31) + (-0.110)(0.08) + 0.020(0.24)
        = 0.0279 + (-0.0088) + 0.0048
        = 0.0239
```

### Gradient w.r.t each vⱼ:
```
∂L/∂vⱼ = αⱼ · d_attn    (scalar × vector)
```

```
∂L/∂v₁ = α₁ · d_attn = 0.3867 × [0.090, -0.110, 0.020]
        = [0.0348, -0.0425, 0.0077]

∂L/∂v₂ = 0.2988 × [0.090, -0.110, 0.020]
        = [0.0269, -0.0329, 0.0060]

∂L/∂v₃ = 0.3145 × [0.090, -0.110, 0.020]
        = [0.0283, -0.0346, 0.0063]
```

---

## Backward Step 3: Gradient through Softmax

We have the gradient w.r.t attention weights: `[∂L/∂α₁, ∂L/∂α₂, ∂L/∂α₃] = [-0.0678, 0.0526, 0.0239]`

Softmax is `αᵢ = exp(sᵢ) / Σⱼ exp(sⱼ)` where `sᵢ` are the scaled scores.

The Jacobian of softmax is:
```
∂αᵢ/∂sⱼ = αᵢ(δᵢⱼ - αⱼ)
```
where `δᵢⱼ = 1` if i=j, else 0.

Given `dα` (our gradient w.r.t α), the gradient w.r.t scores is:
```
d_sⱼ = Σᵢ dαᵢ · αᵢ(δᵢⱼ - αⱼ)
      = αⱼ(dαⱼ - Σᵢ αᵢ·dαᵢ)
```

First compute the sum term (same for all j):
```
Σᵢ αᵢ·dαᵢ = 0.3867(-0.0678) + 0.2988(0.0526) + 0.3145(0.0239)
           = -0.0262 + 0.0157 + 0.0075
           = -0.0030
```

Now for each j:
```
d_s₁ = α₁(dα₁ - (-0.0030)) = 0.3867(-0.0678 + 0.0030)
     = 0.3867(-0.0648)
     = -0.0251

d_s₂ = α₂(dα₂ - (-0.0030)) = 0.2988(0.0526 + 0.0030)
     = 0.2988(0.0556)
     = 0.0166

d_s₃ = α₃(dα₃ - (-0.0030)) = 0.3145(0.0239 + 0.0030)
     = 0.3145(0.0269)
     = 0.0085
```

Check sum should be 0: `-0.0251 + 0.0166 + 0.0085 = 0.0000` ✓

These are gradients w.r.t the **scaled** scores (after division by √d).

---

## Backward Step 4: Through Scaling

We had: `scaled_scores = raw_scores / √d`

```
d_raw_scores = d_scaled_scores / √d
             = [-0.0251/1.732, 0.0166/1.732, 0.0085/1.732]
             = [-0.0145, 0.0096, 0.0049]
```

These are `∂L/∂(scores)` for position 3 attending to positions 1, 2, 3.

---

## Backward Step 5: Gradient through Q·K dot products

We had: `score₃ⱼ = q₃ · kⱼ`

### Gradient w.r.t q₃:
```
∂L/∂q₃ = Σⱼ (∂L/∂score₃ⱼ) · kⱼ
```

```
∂L/∂q₃ = (-0.0145)·k₁ + (0.0096)·k₂ + (0.0049)·k₃
```

Component-wise:

**Dimension 0:**
```
(-0.0145)(0.40) + (0.0096)(-0.14) + (0.0049)(0.05)
= -0.0058 + (-0.0013) + 0.0002
= -0.0069
```

**Dimension 1:**
```
(-0.0145)(0.01) + (0.0096)(0.49) + (0.0049)(-0.22)
= -0.0001 + 0.0047 + (-0.0011)
= 0.0035
```

**Dimension 2:**
```
(-0.0145)(-0.18) + (0.0096)(0.21) + (0.0049)(0.39)
= 0.0026 + 0.0020 + 0.0019
= 0.0065
```

```
∂L/∂q₃ = [-0.0069, 0.0035, 0.0065]
```

### Gradient w.r.t each kⱼ:
```
∂L/∂kⱼ = (∂L/∂score₃ⱼ) · q₃
```

```
∂L/∂k₁ = (-0.0145) × [0.48, -0.09, -0.37]
        = [-0.0070, 0.0013, 0.0054]

∂L/∂k₂ = 0.0096 × [0.48, -0.09, -0.37]
        = [0.0046, -0.0009, -0.0036]

∂L/∂k₃ = 0.0049 × [0.48, -0.09, -0.37]
        = [0.0024, -0.0004, -0.0018]
```

These are the gradients flowing back to the K vectors of each position **from position 3's query**. In a full attention computation, position 3's K also gets gradients from other positions' queries (positions 1 and 2 also attend to it).

---

## Backward Step 6: Gradient through projection matrices

### Gradients for W_Q, W_K, W_V

We have `∂L/∂q₃`, `∂L/∂k₁`, `∂L/∂k₂`, `∂L/∂k₃`, and `∂L/∂v₁`, `∂L/∂v₂`, `∂L/∂v₃`.

Now we compute how these propagate to the weight matrices.

Recall:
```
qᵢ = xᵢ @ W_Q
kᵢ = xᵢ @ W_K  
vᵢ = xᵢ @ W_V
```

### Gradient w.r.t W_Q (only position 3 contributes):
```
∂L/∂W_Q = x₃^T ⊗ (∂L/∂q₃)
```
This is an outer product: `(6,1) × (1,3) → (6,3)`

x₃ = `[0.4, -0.2, 0.1, 0.9, -0.5, 0.3]`
∂L/∂q₃ = `[-0.0069, 0.0035, 0.0065]`

```
∂L/∂W_Q = [[ 0.4×(-0.0069),  0.4×0.0035,  0.4×0.0065],
           [-0.2×(-0.0069), -0.2×0.0035, -0.2×0.0065],
           [ 0.1×(-0.0069),  0.1×0.0035,  0.1×0.0065],
           [ 0.9×(-0.0069),  0.9×0.0035,  0.9×0.0065],
           [-0.5×(-0.0069), -0.5×0.0035, -0.5×0.0065],
           [ 0.3×(-0.0069),  0.3×0.0035,  0.3×0.0065]]
```

```
∂L/∂W_Q = [[-0.00276,  0.00140,  0.00260],
           [ 0.00138, -0.00070, -0.00130],
           [-0.00069,  0.00035,  0.00065],
           [-0.00621,  0.00315,  0.00585],
           [ 0.00345, -0.00175, -0.00325],
           [-0.00207,  0.00105,  0.00195]]
```

### Gradient w.r.t W_K:
Each position contributes to its own K gradient.

```
∂L/∂W_K = Σᵢ xᵢ^T ⊗ (∂L/∂kᵢ)    for i = 1,2,3
```

From position 1: `x₁^T ⊗ ∂L/∂k₁`
```
x₁ = [0.2, 0.5, -0.3, 0.1, 0.8, -0.2]
∂L/∂k₁ = [-0.0070, 0.0013, 0.0054]

= [[ 0.2×(-0.0070), 0.2×0.0013, 0.2×0.0054],
   [ 0.5×(-0.0070), 0.5×0.0013, 0.5×0.0054],
   ...
   ]

= [[-0.00140, 0.00026, 0.00108],
   [-0.00350, 0.00065, 0.00270],
   [ 0.00210, -0.00039, -0.00162],
   [-0.00070, 0.00013, 0.00054],
   [-0.00560, 0.00104, 0.00432],
   [ 0.00140, -0.00026, -0.00108]]
```

From position 2: `x₂^T ⊗ ∂L/∂k₂`
```
∂L/∂k₂ = [0.0046, -0.0009, -0.0036]

= [[-0.1×0.0046, -0.1×(-0.0009), -0.1×(-0.0036)],
   [ 0.3×0.0046,  0.3×(-0.0009),  0.3×(-0.0036)],
   [ 0.6×0.0046,  0.6×(-0.0009),  0.6×(-0.0036)],
   [-0.4×0.0046, -0.4×(-0.0009), -0.4×(-0.0036)],
   [ 0.2×0.0046,  0.2×(-0.0009),  0.2×(-0.0036)],
   [ 0.7×0.0046,  0.7×(-0.0009),  0.7×(-0.0036)]]

= [[-0.00046,  0.00009,  0.00036],
   [ 0.00138, -0.00027, -0.00108],
   [ 0.00276, -0.00054, -0.00216],
   [-0.00184,  0.00036,  0.00144],
   [ 0.00092, -0.00018, -0.00072],
   [ 0.00322, -0.00063, -0.00252]]
```

From position 3: `x₃^T ⊗ ∂L/∂k₃`
```
∂L/∂k₃ = [0.0024, -0.0004, -0.0018]

= [[ 0.4×0.0024,  0.4×(-0.0004),  0.4×(-0.0018)],
   [-0.2×0.0024, -0.2×(-0.0004), -0.2×(-0.0018)],
   [ 0.1×0.0024,  0.1×(-0.0004),  0.1×(-0.0018)],
   [ 0.9×0.0024,  0.9×(-0.0004),  0.9×(-0.0018)],
   [-0.5×0.0024, -0.5×(-0.0004), -0.5×(-0.0018)],
   [ 0.3×0.0024,  0.3×(-0.0004),  0.3×(-0.0018)]]

= [[ 0.00096, -0.00016, -0.00072],
   [-0.00048,  0.00008,  0.00036],
   [ 0.00024, -0.00004, -0.00018],
   [ 0.00216, -0.00036, -0.00162],
   [-0.00120,  0.00020,  0.00090],
   [ 0.00072, -0.00012, -0.00054]]
```

Sum for `∂L/∂W_K`:
```
∂L/∂W_K = pos1 + pos2 + pos3

= [[-0.00140+(-0.00046)+0.00096, 0.00026+0.00009+(-0.00016), 0.00108+0.00036+(-0.00072)],
   [-0.00350+0.00138+(-0.00048), 0.00065+(-0.00027)+0.00008, 0.00270+(-0.00108)+0.00036],
   [ 0.00210+0.00276+0.00024,    -0.00039+(-0.00054)+(-0.00004), -0.00162+(-0.00216)+(-0.00018)],
   [-0.00070+(-0.00184)+0.00216,  0.00013+0.00036+(-0.00036),    0.00054+0.00144+(-0.00162)],
   [-0.00560+0.00092+(-0.00120),  0.00104+(-0.00018)+0.00020,    0.00432+(-0.00072)+0.00090],
   [ 0.00140+0.00322+0.00072,    -0.00026+(-0.00063)+(-0.00012), -0.00108+(-0.00252)+(-0.00054)]]

= [[-0.00090,  0.00019,  0.00072],
   [-0.00260,  0.00046,  0.00198],
   [ 0.00510, -0.00097, -0.00396],
   [-0.00038,  0.00013,  0.00036],
   [-0.00588,  0.00106,  0.00450],
   [ 0.00534, -0.00101, -0.00414]]
```

### Gradient w.r.t W_V:
Similarly, each position contributes.

```
∂L/∂W_V = Σᵢ xᵢ^T ⊗ (∂L/∂vᵢ)    for i = 1,2,3
```

```
∂L/∂v₁ = [0.0348, -0.0425, 0.0077]
∂L/∂v₂ = [0.0269, -0.0329, 0.0060]
∂L/∂v₃ = [0.0283, -0.0346, 0.0063]
```

From pos1: `x₁^T ⊗ ∂L/∂v₁`
```
[[ 0.2×0.0348,  0.2×(-0.0425),  0.2×0.0077],
 [ 0.5×0.0348,  0.5×(-0.0425),  0.5×0.0077],
 [-0.3×0.0348, -0.3×(-0.0425), -0.3×0.0077],
 [ 0.1×0.0348,  0.1×(-0.0425),  0.1×0.0077],
 [ 0.8×0.0348,  0.8×(-0.0425),  0.8×0.0077],
 [-0.2×0.0348, -0.2×(-0.0425), -0.2×0.0077]]

= [[ 0.00696, -0.00850,  0.00154],
   [ 0.01740, -0.02125,  0.00385],
   [-0.01044,  0.01275, -0.00231],
   [ 0.00348, -0.00425,  0.00077],
   [ 0.02784, -0.03400,  0.00616],
   [-0.00696,  0.00850, -0.00154]]
```

From pos2: `x₂^T ⊗ ∂L/∂v₂`
```
[[-0.1×0.0269, -0.1×(-0.0329), -0.1×0.0060],
 [ 0.3×0.0269,  0.3×(-0.0329),  0.3×0.0060],
 [ 0.6×0.0269,  0.6×(-0.0329),  0.6×0.0060],
 [-0.4×0.0269, -0.4×(-0.0329), -0.4×0.0060],
 [ 0.2×0.0269,  0.2×(-0.0329),  0.2×0.0060],
 [ 0.7×0.0269,  0.7×(-0.0329),  0.7×0.0060]]

= [[-0.00269,  0.00329, -0.00060],
   [ 0.00807, -0.00987,  0.00180],
   [ 0.01614, -0.01974,  0.00360],
   [-0.01076,  0.01316, -0.00240],
   [ 0.00538, -0.00658,  0.00120],
   [ 0.01883, -0.02303,  0.00420]]
```

From pos3: `x₃^T ⊗ ∂L/∂v₃`
```
[[ 0.4×0.0283,  0.4×(-0.0346),  0.4×0.0063],
 [-0.2×0.0283, -0.2×(-0.0346), -0.2×0.0063],
 [ 0.1×0.0283,  0.1×(-0.0346),  0.1×0.0063],
 [ 0.9×0.0283,  0.9×(-0.0346),  0.9×0.0063],
 [-0.5×0.0283, -0.5×(-0.0346), -0.5×0.0063],
 [ 0.3×0.0283,  0.3×(-0.0346),  0.3×0.0063]]

= [[ 0.01132, -0.01384,  0.00252],
   [-0.00566,  0.00692, -0.00126],
   [ 0.00283, -0.00346,  0.00063],
   [ 0.02547, -0.03114,  0.00567],
   [-0.01415,  0.01730, -0.00315],
   [ 0.00849, -0.01038,  0.00189]]
```

Sum for `∂L/∂W_V`:
```
= [[ 0.00696+(-0.00269)+0.01132, -0.00850+0.00329+(-0.01384),  0.00154+(-0.00060)+0.00252],
   [ 0.01740+0.00807+(-0.00566), -0.02125+(-0.00987)+0.00692,  0.00385+0.00180+(-0.00126)],
   [-0.01044+0.01614+0.00283,    0.01275+(-0.01974)+(-0.00346), -0.00231+0.00360+0.00063],
   [ 0.00348+(-0.01076)+0.02547, -0.00425+0.01316+(-0.03114),   0.00077+(-0.00240)+0.00567],
   [ 0.02784+0.00538+(-0.01415), -0.03400+(-0.00658)+0.01730,   0.00616+0.00120+(-0.00315)],
   [-0.00696+0.01883+0.00849,    0.00850+(-0.02303)+(-0.01038), -0.00154+0.00420+0.00189]]

= [[ 0.01559, -0.01905,  0.00346],
   [ 0.01981, -0.02420,  0.00439],
   [ 0.00853, -0.01045,  0.00192],
   [ 0.01819, -0.02223,  0.00404],
   [ 0.01907, -0.02328,  0.00421],
   [ 0.02036, -0.02491,  0.00455]]
```

---

## Backward Step 7: Gradient through to Input X

We also need gradients flowing to the input `X` (which will flow further backward).

For each position, input `xᵢ` contributes to `qᵢ`, `kᵢ`, and `vᵢ`:
```
∂L/∂xᵢ = (∂L/∂qᵢ) @ W_Q^T + (∂L/∂kᵢ) @ W_K^T + (∂L/∂vᵢ) @ W_V^T
```

For position 3:
```
∂L/∂q₃ = [-0.0069, 0.0035, 0.0065]
∂L/∂k₃ = [0.0024, -0.0004, -0.0018]
∂L/∂v₃ = [0.0283, -0.0346, 0.0063]
```

This would produce a `(6,)` vector that gets added to the residual gradient and flows backward through the network.

*(I'll skip the full matrix multiplication for x₃ here for brevity, but the pattern is the same as all projections we've done.)*

---

# SUMMARY: THE COMPLETE FLOW

```
FORWARD:
x₁,x₂,x₃ → Q,K,V (via W_Q, W_K, W_V)
q₃ · k₁,k₂,k₃ → raw scores
raw scores / √d → scaled scores
softmax(scaled scores) → α₁,α₂,α₃
α₁v₁ + α₂v₂ + α₃v₃ → attention output (3D)
attention output @ W_O → final output (6D)

BACKWARD:
∂L/∂output (6D)
  → ∂L/∂W_O (3×6) and ∂L/∂(attention output) (3D)
  → ∂L/∂α (3) and ∂L/∂vᵢ (3 each)
  → ∂L/∂(scaled scores) (3) via softmax Jacobian
  → ∂L/∂(raw scores) (3) via scaling
  → ∂L/∂q₃ (3) and ∂L/∂kᵢ (3 each)
  → ∂L/∂W_Q (6×3), ∂L/∂W_K (6×3), ∂L/∂W_V (6×3)
  → ∂L/∂xᵢ (6 each) flows further backward
```

Every weight matrix gets a gradient update, and every input embedding gets a gradient that propagates all the way back through the network to the first layer and ultimately to the token embeddings.

---

Does this concrete walkthrough give you a solid intuition for the mechanics? I can show the same thing for multi-head attention (how heads combine), or walk through how these gradients actually update the weights in the AdamW optimizer step with specific numbers.

# -----------------------------------------------------
I'll walk through the AdamW optimizer step with concrete numbers, continuing from our toy model. This will show exactly how those gradients we computed become actual weight changes.

---

# SETUP: THE GRADIENTS WE HAVE

After backpropagation through the entire network (all layers, all positions), we've accumulated gradients for all weight matrices. Let's focus on one specific weight matrix from our attention layer:

**W_Q** — the query projection matrix.

Current value (from before):
```
W_Q = [[ 0.1,  0.2, -0.1],
       [-0.1,  0.3,  0.2],
       [ 0.2, -0.1,  0.1],
       [ 0.3,  0.1, -0.2],
       [-0.2,  0.2,  0.3],
       [ 0.1, -0.3,  0.1]]
```

After backpropagation through the **entire batch** (all 4 sequences, all 4096 positions each), we've averaged the gradients across all tokens. Let's say the **mean gradient** (averaged over the global batch of 16,384 tokens) for W_Q is:

```
g_raw = [[-0.0030,  0.0015,  0.0028],
         [ 0.0015, -0.0008, -0.0014],
         [-0.0008,  0.0004,  0.0007],
         [-0.0068,  0.0035,  0.0064],
         [ 0.0038, -0.0019, -0.0036],
         [-0.0023,  0.0012,  0.0021]]
```

(This is the gradient we computed for W_Q in the backward pass, now scaled up to reflect averaging over the full batch.)

---

# PART 1: THE OPTIMIZER STATE

AdamW maintains two moving averages for **every single weight** in the network. Let's focus on just **one element** of W_Q to see the full trajectory:

**Weight at position [0,0]**: `W_Q[0,0] = 0.1`

This is the weight in row 0, column 0 of the query projection matrix.

## 1.1 Initial State

At the start of training (or right after initialization), the optimizer state for this weight is:

```
m₀ = 0.0    (first moment estimate — momentum)
v₀ = 0.0    (second moment estimate — velocity)
```

We're currently at **step t = 10,000** (the model has already done 9,999 optimizer updates). The running state for this weight is:

```
m_{9999} = -0.0021
v_{9999} = 0.000045
```

These have been tracking the gradient signal for this weight over all previous steps.

---

# PART 2: THE GRADIENT AT THIS STEP

For weight `W_Q[0,0]`, the raw gradient from this batch is:

```
g_raw[0,0] = -0.0030
```

## 2.1 Weight Decay

AdamW **decouples** weight decay from the adaptive learning rate. This is the key difference from standard Adam.

We first add the weight decay contribution:

```
λ = 0.1    (weight decay factor — typical values: 0.1 for small models, 0.01-0.001 for large LLMs)

g_decay = λ × W_current = 0.1 × 0.1 = 0.01
```

Wait — that seems huge. Let me use realistic values. For LLM pretraining, weight decay is much smaller:

```
λ = 0.001   (more realistic for large models)

g_decay = 0.001 × 0.1 = 0.0001
```

Total gradient for this weight:

```
g = g_raw + g_decay = -0.0030 + 0.0001 = -0.0029
```

This addition of `λ × W` to the gradient is what pulls weights toward zero (regularization), but in AdamW it's applied **outside** the moment estimates, which is crucial.

---

# PART 3: UPDATING MOMENT ESTIMATES

AdamW hyperparameters:
```
β₁ = 0.9     (momentum decay — how much to remember past gradients)
β₂ = 0.95    (velocity decay — how much to remember past squared gradients)  
             (LLMs often use 0.95 instead of the standard 0.999)
ε = 1e-8     (numerical stability constant)
```

## 3.1 Update First Moment (m)

```
m_t = β₁ × m_{t-1} + (1 - β₁) × g
```

For our weight:
```
m_{10000} = 0.9 × (-0.0021) + (1 - 0.9) × (-0.0029)
         = -0.00189 + 0.1 × (-0.0029)
         = -0.00189 + (-0.00029)
         = -0.00218
```

Interpretation: The momentum is a **leaky average** of past gradients. 90% of the previous momentum is kept, 10% of the new gradient is incorporated. This smooths out noisy per-batch gradients.

## 3.2 Update Second Moment (v)

```
v_t = β₂ × v_{t-1} + (1 - β₂) × g²
```

```
v_{10000} = 0.95 × 0.000045 + (1 - 0.95) × (-0.0029)²
         = 0.00004275 + 0.05 × 0.00000841
         = 0.00004275 + 0.0000004205
         = 0.00004317
```

Interpretation: This tracks the **variance** of gradients. A large v means this weight's gradients have been volatile; a small v means they've been consistent.

---

# PART 4: BIAS CORRECTION

Since m and v were initialized at 0, they're biased toward zero in early steps. The correction:

## 4.1 First Moment Correction

```
m̂ = m_t / (1 - β₁^t)
```

At step 10,000:
```
β₁^10000 = 0.9^10000 
```

This is effectively zero — `0.9^10000 ≈ 10^(-459)`. So:

```
m̂ ≈ m_t / (1 - 0)
   = m_t / 1
   = -0.00218
```

But in early steps (let's see step 10):
```
β₁^10 = 0.9^10 ≈ 0.3487
m̂ = m_t / (1 - 0.3487) = m_t / 0.6513
```

This amplification matters early in training but becomes negligible after a few thousand steps.

## 4.2 Second Moment Correction

Similarly:
```
v̂ = v_t / (1 - β₂^t)
```

At step 10,000, `0.95^10000 ≈ 0`, so `v̂ ≈ 0.00004317`.

---

# PART 5: THE WEIGHT UPDATE

The Adam update rule:

```
W_new = W_old - η × m̂ / (√v̂ + ε)
```

## 5.1 Learning Rate

We're at step 10,000 of 250,000 total steps. The learning rate follows a cosine schedule:

```
η_max = 3e-4   (0.0003)
η_min = 3e-5   (0.00003) = 10% of max

progress = step / total_steps = 10000 / 250000 = 0.04

η = η_min + 0.5 × (η_max - η_min) × (1 + cos(π × progress))
  = 0.00003 + 0.5 × (0.00027) × (1 + cos(π × 0.04))
  = 0.00003 + 0.000135 × (1 + cos(0.1257))
  = 0.00003 + 0.000135 × (1 + 0.9921)
  = 0.00003 + 0.000135 × 1.9921
  = 0.00003 + 0.0002689
  = 0.0002989
```

We're still near the peak learning rate (early in training).

## 5.2 The Update Calculation

```
W_new = 0.1 - 0.0002989 × (-0.00218) / (√0.00004317 + 1e-8)
```

Step by step:
```
√v̂ = √0.00004317 = 0.006571
√v̂ + ε = 0.006571 + 0.00000001 = 0.006571

m̂ / (√v̂ + ε) = -0.00218 / 0.006571 = -0.3318

η × (that) = 0.0002989 × (-0.3318) = -0.0000992

W_new = 0.1 - (-0.0000992)
      = 0.1 + 0.0000992
      = 0.1000992
```

**The weight increased from 0.1000000 to 0.1000992.**

---

# PART 6: WHY THIS WORKED THE WAY IT DID

Let's interpret what happened:

1. **The gradient was negative** (`g = -0.0029`): This means increasing this weight would **reduce** the loss.

2. **Momentum was also negative** (`m = -0.00218`): The historical trend also says "increase this weight."

3. **Velocity was small** (`v = 0.00004317`): This weight's gradients haven't been too volatile, so Adam is willing to take a meaningful step.

4. **The effective step direction** (`m̂/√v̂ = -0.3318`) is a **normalized** version of the momentum. The division by `√v̂` adaptively scales the step — weights with high variance get smaller steps, weights with low variance get larger steps.

5. **The weight increased**, which aligns with the negative gradient.

---

# PART 7: THE FULL MATRIX UPDATE

Let's see the entire W_Q matrix update in parallel. Here's every element's computation:

## Gradient (g_raw + weight_decay):

Weight decay contribution per element: `λ × W[i,j]`
```
g_decay = 0.001 × W_Q

= [[ 0.0001,  0.0002, -0.0001],
   [-0.0001,  0.0003,  0.0002],
   [ 0.0002, -0.0001,  0.0001],
   [ 0.0003,  0.0001, -0.0002],
   [-0.0002,  0.0002,  0.0003],
   [ 0.0001, -0.0003,  0.0001]]
```

Total gradient `g = g_raw + g_decay`:
```
g = [[-0.0029,  0.0017,  0.0027],
     [ 0.0014, -0.0005, -0.0012],
     [-0.0006,  0.0003,  0.0008],
     [-0.0065,  0.0036,  0.0062],
     [ 0.0036, -0.0017, -0.0033],
     [-0.0022,  0.0009,  0.0022]]
```

## Previous Momentum (m_{9999}):
```
m_prev = [[-0.0021,  0.0012,  0.0020],
          [ 0.0011, -0.0004, -0.0009],
          [-0.0005,  0.0002,  0.0006],
          [-0.0048,  0.0026,  0.0046],
          [ 0.0027, -0.0013, -0.0025],
          [-0.0017,  0.0007,  0.0016]]
```

## New Momentum (m_t = 0.9 × m_prev + 0.1 × g):
```
m_t = [[-0.00218,  0.00125,  0.00207],
       [ 0.00113, -0.00041, -0.00093],
       [-0.00051,  0.00021,  0.00062],
       [-0.00497,  0.00270,  0.00476],
       [ 0.00279, -0.00134, -0.00258],
       [-0.00175,  0.00072,  0.00166]]
```

## Previous Velocity (v_{9999}):
```
v_prev = [[4.5e-5, 1.8e-5, 3.2e-5],
          [1.5e-5, 2.0e-5, 1.1e-5],
          [3.0e-5, 1.2e-5, 2.8e-5],
          [5.8e-5, 2.5e-5, 5.2e-5],
          [3.5e-5, 1.9e-5, 4.0e-5],
          [2.2e-5, 1.4e-5, 2.9e-5]]
```

## New Velocity (v_t = 0.95 × v_prev + 0.05 × g²):
```
v_t = [[4.32e-5, 1.72e-5, 3.08e-5],
       [1.43e-5, 1.90e-5, 1.05e-5],
       [2.85e-5, 1.14e-5, 2.66e-5],
       [5.72e-5, 2.44e-5, 5.13e-5],
       [3.39e-5, 1.82e-5, 3.85e-5],
       [2.11e-5, 1.33e-5, 2.78e-5]]
```

## Effective Step (m̂ / (√v̂ + ε)):
```
step = [[-0.332,  0.302,  0.374],
        [ 0.298, -0.095, -0.287],
        [-0.095,  0.062,  0.120],
        [-0.657,  0.548,  0.665],
        [ 0.480, -0.315, -0.416],
        [-0.381,  0.198,  0.315]]
```

## Learning Rate: `η = 0.0002989`

## Weight Update (η × step):
```
ΔW = [[-0.000099,  0.000090,  0.000112],
      [ 0.000089, -0.000028, -0.000086],
      [-0.000028,  0.000019,  0.000036],
      [-0.000196,  0.000164,  0.000199],
      [ 0.000143, -0.000094, -0.000124],
      [-0.000114,  0.000059,  0.000094]]
```

## New W_Q:
```
W_Q_new = [[ 0.0999,  0.2001, -0.0999],
           [-0.0999,  0.3000,  0.1999],
           [ 0.2000, -0.1000,  0.1000],
           [ 0.2998,  0.1002, -0.1998],
           [-0.1999,  0.1999,  0.2999],
           [ 0.0999, -0.2999,  0.1001]]
```

---

# PART 8: WHY ADAMW OVER ADAM?

The critical difference:

**Standard Adam (with L2 regularization):**
```
g_total = g_raw + λ × W
m = β₁×m + (1-β₁)×g_total    ← weight decay enters moment estimates
v = β₂×v + (1-β₂)×g_total²
W = W - η × m̂/(√v̂ + ε)
```

**AdamW (decoupled weight decay):**
```
g_total = g_raw                  ← gradient is pure
m = β₁×m + (1-β₁)×g_raw
v = β₂×v + (1-β₂)×g_raw²
W = W - η × m̂/(√v̂ + ε) - η × λ × W    ← weight decay applied separately
```

The problem with standard Adam: weight decay gets **diluted** by the adaptive scaling. Weights that naturally have large gradients (and thus large `√v̂`) get **less** regularization. AdamW fixes this by making weight decay uniform across all weights, independent of their gradient history.

---

# PART 9: TRAJECTORY OVER STEPS

Let's track our weight `W_Q[0,0]` over multiple steps to see the dynamics:

```
Step 9999:  W = 0.1000000,  m = -0.00210,  v = 0.00004500
Step 10000: W = 0.1000992,  m = -0.00218,  v = 0.00004317
```

If gradients continue to be negative (pushing the weight up):

```
Step 10001: g = -0.0031
            m = 0.9(-0.00218) + 0.1(-0.0031) = -0.00227
            v = 0.95(0.00004317) + 0.05(0.00000961) = 0.00004107
            W = 0.1000992 - 0.0002989(-0.00227)/√0.00004107
              = 0.1000992 + 0.000106
              = 0.1002052

Step 10002: g = -0.0028
            m = 0.9(-0.00227) + 0.1(-0.0028) = -0.00232
            v = 0.95(0.00004107) + 0.05(0.00000784) = 0.00003912
            W = 0.1002052 + 0.0002989(0.00232)/√0.00003912
              = 0.1002052 + 0.000111
              = 0.1003162
```

The weight is steadily climbing, with momentum smoothing the path and the adaptive learning rate keeping steps sized appropriately.

---

# PART 10: SPECIAL CASES AND PHENOMENA

## 10.1 What Happens When a Gradient is Zero?

If `g = 0` for many steps:
```
m decays toward 0:  m_t = 0.9 × m_{t-1} + 0.1 × 0
v decays toward 0:  v_t = 0.95 × v_{t-1} + 0.05 × 0
```
But v decays faster than m (β₂ is larger), so `m̂/√v̂` can temporarily spike. The `ε` term prevents division by zero.

## 10.2 What Happens with Consistently Large Gradients?

If `|g|` is always around 0.01:
```
v will stabilize around E[g²] ≈ 0.0001
√v ≈ 0.01
m̂ ≈ E[g] ≈ 0.01 (or -0.01)
m̂/√v̂ ≈ 1.0
```
The effective step is bounded near 1.0, regardless of gradient magnitude. This **normalization** is Adam's superpower — it makes the effective step size roughly constant across all weights, even when some have naturally tiny gradients and others have huge ones.

## 10.3 Gradient Clipping

Before AdamW even sees the gradient, the **global gradient norm** is clipped. For our tiny W_Q, this doesn't matter, but for the full 7B parameter model:

```
Total gradient norm = √(Σ_all_params g²)

If this > max_norm (typically 1.0):
    Scale all gradients by max_norm / total_norm
```

This prevents any single batch from causing a destructively large update.

---

# COMPLETE ADAMW ALGORITHM (Per Weight)

```
Input: W (weight), g (gradient), m (momentum), v (velocity)
        η (learning rate), λ (weight decay)
        β₁, β₂, ε, t (step count)

1. g = g + λ × W                              # Decoupled weight decay

2. m = β₁ × m + (1 - β₁) × g                  # Update biased first moment
3. v = β₂ × v + (1 - β₂) × g²                 # Update biased second moment

4. m̂ = m / (1 - β₁^t)                         # Bias correction
5. v̂ = v / (1 - β₂^t)

6. W = W - η × m̂ / (√v̂ + ε)                    # Weight update

Output: W, m, v
```

This algorithm runs for **every one of the 7 billion parameters**, for **250,000 steps**, processing **trillions of tokens**, across **thousands of GPUs**, for **weeks or months**.

And at the end, what was random noise becomes a compressed representation of human language and knowledge.

---

Does this make the optimizer dynamics clear? Would you like me to show how different hyperparameter choices (β₁, β₂, weight decay, learning rate schedule) affect training behavior, or perhaps contrast AdamW with other optimizers like SGD with momentum or Lion?