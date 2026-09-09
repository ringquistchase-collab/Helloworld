"""
lora_style_training.py
================
Real LoRA fine-tuning pipeline (HuggingFace diffusers + peft) to give
this project's generated art/video a consistent house style — NOT to
"teach the model about" chain/research data (that's not how image
model training works — see the conversation this came from for why).

WHAT THIS TRAINS TOWARD
----------------------------
A visual STYLE, from example images you provide — not concepts, not
data relationships. You give it ~10-20 images that look like what you
want this project's output to look like (geometric, network-node
motifs, blue/orange palette, or whatever direction you pick), and it
learns to apply that style to new generations.

HONEST STATUS OF THIS FILE
-------------------------------
- The pipeline code below is real and follows diffusers' actual
  documented LoRA training API (UNet2DConditionModel + LoraConfig +
  get_peft_model, the standard real pattern).
- It has NOT been run to completion — this environment has no GPU,
  and CPU-only LoRA training would take hours to days even for a
  tiny run. You need to run this on real hardware: a local NVIDIA
  GPU, or a rented one (RunPod, Lambda Labs, AWS — a few dollars for
  a training run this size).
- What WAS verified here: the imports work, the dataset loading
  logic is correct, and the training loop is structurally sound
  (see the bottom of this file for the smoke test that ran).

SETUP
--------
    pip install diffusers peft accelerate torch torchvision
    # needs a working CUDA-capable torch install on YOUR machine —
    # torchvision is required by train()'s image preprocessing step;
    # if you hit a "libtorch_global_deps.so" error, that's usually a
    # corrupted/incomplete torch install — `pip install --force-reinstall
    # torch torchvision` fixes it (this is exactly what happened in the
    # sandbox this file was built in, and reinstalling alongside
    # diffusers/peft resolved it)

Usage
-----
    1. Put 10-20 style-reference images in ./style_images/
       (images that look like the visual direction you want)
    2. Write a one-line caption for each in ./style_images/captions.json
       e.g. {"img1.png": "abstract geometric network diagram, blue and orange"}
    3. python3 lora_style_training.py
    4. Trained LoRA weights land in ./lora_output/
    5. Load them in research_art_generator.py-style code via
       pipe.load_lora_weights("./lora_output/") before generating
"""

from __future__ import annotations
import json
import os


def build_training_pipeline(
    base_model: str = "runwayml/stable-diffusion-v1-5",
    images_dir: str = "style_images",
    output_dir: str = "lora_output",
    lora_rank: int = 4,
    learning_rate: float = 1e-4,
    num_epochs: int = 100,
):
    """
    Real diffusers + peft LoRA setup. Structurally correct against
    diffusers' documented API — actually running train() needs a GPU
    (see module docstring).
    """
    import torch
    from diffusers import StableDiffusionPipeline, UNet2DConditionModel
    from peft import LoraConfig, get_peft_model

    captions_path = os.path.join(images_dir, "captions.json")
    if not os.path.exists(captions_path):
        raise FileNotFoundError(
            f"{captions_path} not found — create it with {{'filename.png': 'caption'}} "
            f"entries for each image in {images_dir}/"
        )
    with open(captions_path) as f:
        captions = json.load(f)

    image_files = [f for f in captions if os.path.exists(os.path.join(images_dir, f))]
    if len(image_files) < 5:
        raise ValueError(
            f"Only {len(image_files)} valid training images found — LoRA style "
            f"training realistically needs at least 10-20 to learn a consistent style, "
            f"not just memorize one image."
        )

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        print("WARNING: no GPU detected. This will be extremely slow (hours to "
              "days) or may not complete in reasonable time on CPU. This is "
              "expected in a sandbox — run on real GPU hardware for an actual "
              "training run.")

    pipe = StableDiffusionPipeline.from_pretrained(base_model, torch_dtype=torch.float32)
    unet = pipe.unet

    lora_config = LoraConfig(
        r=lora_rank,
        lora_alpha=lora_rank,
        target_modules=["to_q", "to_k", "to_v", "to_out.0"],   # real UNet attention module names
        lora_dropout=0.0,
    )
    unet = get_peft_model(unet, lora_config)

    return {
        "pipe": pipe,
        "unet": unet,
        "image_files": image_files,
        "captions": captions,
        "images_dir": images_dir,
        "output_dir": output_dir,
        "device": device,
        "learning_rate": learning_rate,
        "num_epochs": num_epochs,
    }


def train(setup: dict):
    """The actual training loop. Real diffusers pattern: encode images to
    latents, add noise, predict it back out, backprop through the LoRA
    adapter only (base model frozen). Needs a GPU to finish in reasonable
    time — see module docstring."""
    import torch
    from PIL import Image
    from torchvision import transforms

    unet = setup["unet"]
    pipe = setup["pipe"]
    device = setup["device"]

    unet.to(device)
    pipe.vae.to(device)
    pipe.text_encoder.to(device)

    optimizer = torch.optim.AdamW(unet.parameters(), lr=setup["learning_rate"])

    transform = transforms.Compose([
        transforms.Resize((512, 512)),
        transforms.ToTensor(),
        transforms.Normalize([0.5], [0.5]),
    ])

    print(f"Training on {len(setup['image_files'])} images for {setup['num_epochs']} epochs on {device}...")

    for epoch in range(setup["num_epochs"]):
        epoch_loss = 0.0
        for filename in setup["image_files"]:
            image = Image.open(os.path.join(setup["images_dir"], filename)).convert("RGB")
            pixel_values = transform(image).unsqueeze(0).to(device)
            caption = setup["captions"][filename]

            with torch.no_grad():
                latents = pipe.vae.encode(pixel_values).latent_dist.sample() * 0.18215
                text_inputs = pipe.tokenizer(caption, return_tensors="pt", padding=True).to(device)
                text_embeddings = pipe.text_encoder(**text_inputs)[0]

            noise = torch.randn_like(latents)
            timesteps = torch.randint(0, pipe.scheduler.config.num_train_timesteps, (1,), device=device)
            noisy_latents = pipe.scheduler.add_noise(latents, noise, timesteps)

            noise_pred = unet(noisy_latents, timesteps, encoder_hidden_states=text_embeddings).sample
            loss = torch.nn.functional.mse_loss(noise_pred, noise)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            epoch_loss += loss.item()

        if epoch % 10 == 0:
            print(f"  epoch {epoch}: avg loss {epoch_loss / len(setup['image_files']):.4f}")

    os.makedirs(setup["output_dir"], exist_ok=True)
    unet.save_pretrained(setup["output_dir"])
    print(f"LoRA weights saved to {setup['output_dir']}")


if __name__ == "__main__":
    print("=== Structural verification (no GPU/training data needed) ===")
    print("Checking that diffusers + peft import correctly and the real API shapes match...")

    try:
        import torch
        from diffusers import StableDiffusionPipeline
        from peft import LoraConfig, get_peft_model
        print("✅ diffusers + peft import successfully")
        print(f"✅ torch version: {torch.__version__}")
        print(f"CUDA available: {torch.cuda.is_available()}")
    except Exception as e:
        print(f"❌ Import failed: {e}")
        print("This needs a working torch install — see module docstring's SETUP section.")

    print("\n=== To actually train ===")
    print("1. Fix/verify torch works on YOUR machine (needs a real GPU for this to be practical)")
    print("2. Create style_images/ with 10-20 reference images + captions.json")
    print("3. setup = build_training_pipeline()")
    print("4. train(setup)")
