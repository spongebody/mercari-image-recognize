from typing import Optional


def build_showcase_prompt(prompt_hint: Optional[str]) -> str:
    parts = [
        "Create a realistic, high-conversion e-commerce hero image intended as the primary listing thumbnail. The photo must read as a CANDID LIFESTYLE MOMENT of the product being used in real life — NOT a posed studio showcase, NOT a catalog stand-and-display.",
        "[PRODUCT FIDELITY — HIGHEST PRIORITY] Strictly preserve the product's exact color, material, texture, structure, proportions, stitching, prints, hardware, logo placement, and all signature details from the reference. Do NOT restyle, recolor, \"beautify\", or alter the product in any way. The product must be visually identical to the source.",
        "[CORE DIRECTION — A MOMENT, NOT A POSE] Frame the shot as if a friend casually snapped the model mid-action: stepping out of a cafe, walking down a sunlit sidewalk, pausing at a doorway, hailing a friend across the street, browsing a market stall. The model should look lived-in and unposed — natural weight shift, soft micro-expression, real eye contact or a subtle off-camera glance with intent. No T-pose, no symmetrical mannequin stance, no dead-eyed forward stare, no obvious \"I am modeling now\" energy.",
        "[NATURAL INTERACTION WITH THE PRODUCT] The model must INTERACT with the product the exact way a real customer would use it. Hands and body should engage with it authentically — this is what makes the shot feel real.",
        "Bags: carried by the top handle in hand, hooked in the elbow crook, slung on one shoulder, or worn crossbody — pick whichever silhouette suits the bag type. The bag's FRONT FACE (logo / signature side) must rotate toward the camera. The grip is relaxed, fingers naturally curved on the handle/strap, never claw-like or stiff.",
        "Clothing: worn while standing relaxed with weight on one leg, walking, or pausing mid-step. Visible natural fabric movement and drape. Hands can be in pockets, holding a coffee cup, adjusting a sleeve, brushing hair — anything that reads human.",
        "Shoes: caught mid-step or in a relaxed stance, at least one shoe shown clearly at a flattering 3/4 or front angle.",
        "Accessories (hats, scarves, jewelry, sunglasses): worn integrated into a real outfit, framed so the item reads clearly without being awkwardly \"presented\".",
        "Furniture / home goods: stage a real in-use vignette — someone reading in the chair, a half-finished cup of tea on the table, a throw blanket draped naturally — with the product's main face fully visible and centered as the hero.",
        "[FRAMING — KEEP THE PRODUCT AS HERO] Even though it's a candid moment, the product remains the clear visual anchor. Product shown from its PRIMARY DISPLAY ANGLE (the angle a shopper expects to recognize it from). Product face / logo / signature detail unobstructed by hair, hands, props, or clothing folds. Model's torso and face oriented toward the camera (front-facing, or at most a slight 10-20 degree turn) so it still works as a marketplace thumbnail. Full-body framing with comfortable headroom and footroom; product positioned in the upper-middle to middle of the composition where the eye lands first.",
        "[LIGHTING & SCENE] Soft, natural daylight — window light, overcast diffuse light, or warm golden-hour sun. Real lifestyle location that matches the product's vibe (sunlit street, cafe exterior, leafy park path, modern apartment doorway, train platform, neighborhood market). Background slightly out of focus (35mm or 50mm lens, approximately f/2.8-f/4) but readable as a real place. No clutter, no competing branding or signage, no on-screen text.",
        "[REALISM — REJECT AI TELLS] Photographic DSLR realism. True-to-life skin with visible pores and natural texture — NO plastic smoothing, NO airbrushed skin, NO symmetrical \"AI face\". Real fabric wrinkles, real ambient + contact shadows, accurate hand anatomy (five fingers, correct proportions, no fused or melted fingers, no warped wrists). No HDR over-saturation, no glow, no uncanny eyes, no over-sharpened edges.",
        "[ENERGY] Effortlessly premium and aspirational — the kind of shot that makes a shopper stop scrolling. Confident, relaxed, modern, like a real person you'd want to follow.",
        "[OUTPUT] A SINGLE commercial photograph. No collage, no split frames, no side-by-side, no watermark, no logo overlay, no text, no caption, no border, no poster layout.",
    ]
    if prompt_hint:
        parts.append(f"Additional guidance: {prompt_hint}")
    return " ".join(parts)
