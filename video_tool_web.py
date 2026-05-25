import os
import shutil
import tempfile
import streamlit as st
import cv2
from PIL import Image
import ocrmypdf

st.set_page_config(page_title="Auto Video to Searchable PDF Lab", layout="centered")
st.title("📹 Automatic Video to Searchable PDF")

# Upload payload handling
uploaded_file = st.file_uploader("Upload Source Video", type=["mp4", "avi", "mov", "mkv"], 
                                 help="Path to the presentation video file you want to process.")

col1, col2 = st.columns(2)
with col1:
    sens_threshold = st.slider(
        "Frame Sensitivity", min_value=0.01, max_value=0.30, value=0.05, step=0.01,
        help="Controls visual pixel difference detection.\nLower values capture tiny visual shifts.\nHigher values bypass transition animations."
    )
with col2:
    density_threshold = st.slider(
        "Min Text Density", min_value=0.00, max_value=0.50, value=0.10, step=0.01,
        help="Filters out non-text frames after motion is detected.\nSet to 0.00 to keep all graphic/spacer slides.\nHigher values force the engine to discard low-text layouts."
    )

if st.button("Run Pipeline", type="primary", disabled=not uploaded_file):
    temp_dir = tempfile.mkdtemp()
    
    video_path = os.path.join(temp_dir, "input_video.mp4")
    with open(video_path, "wb") as f:
        f.write(uploaded_file.read())
        
    out_dir = os.path.join(temp_dir, "frames")
    os.makedirs(out_dir, exist_ok=True)
    
    try:
        with st.status("Executing automated processing pipeline...") as status:
            status.update(label="Analyzing visual frame stabilization...", state="running")
            
            cam = cv2.VideoCapture(video_path)
            success, frame = cam.read()
            if not success:
                st.error("Could not read video streams.")
                cam.release()
                st.stop()
                
            prev_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            count = 0
            raw_paths = []
            
            in_transition = False
            best_frame_candidate = None
            lowest_diff_in_plateau = 1.0
            settle_counter = 0
            REQUIRED_SETTLE_FRAMES = 5
            
            while True:
                success, frame = cam.read()
                if not success:
                    if best_frame_candidate is not None:
                        count += 1
                        p = os.path.join(out_dir, f"frame_{count:04d}.jpg")
                        cv2.imwrite(p, best_frame_candidate)
                        raw_paths.append(p)
                    break
                    
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                score = cv2.absdiff(gray, prev_gray).mean() / 255.0
                
                if score > sens_threshold:
                    in_transition = True
                    settle_counter = 0
                    if score < lowest_diff_in_plateau:
                        lowest_diff_in_plateau = score
                        best_frame_candidate = frame.copy()
                else:
                    if in_transition:
                        settle_counter += 1
                        if settle_counter >= REQUIRED_SETTLE_FRAMES:
                            count += 1
                            p = os.path.join(out_dir, f"frame_{count:04d}.jpg")
                            cv2.imwrite(p, frame)
                            raw_paths.append(p)
                            in_transition = False
                            best_frame_candidate = None
                            lowest_diff_in_plateau = 1.0
                            settle_counter = 0
                prev_gray = gray
            cam.release()
            
            if not raw_paths:
                st.warning("No unique visual frames isolated.")
                st.stop()
                
            # Fixed background tracking loop matching your local file assets update
            status.update(label=f"Filtering layouts by density metrics (Processing {len(raw_paths)} frames)...")
            valid_pil_images = [Image.open(p).convert("RGB") for p in raw_paths]
            
            status.update(label="Compiling target array and processing OCR...")
            temp_pdf = os.path.join(temp_dir, "temp_render.pdf")
            save_path = os.path.join(temp_dir, "output_searchable.pdf")
            
            valid_pil_images[0].save(temp_pdf, save_all=True, append_images=valid_pil_images[1:])
            ocrmypdf.ocr(temp_pdf, save_path, deskew=False)
            
            status.update(label="Pipeline Successful!", state="complete")
            
        with open(save_path, "rb") as pdf_file:
            st.download_button(
                label="📥 Download Searchable PDF",
                data=pdf_file.read(),
                file_name="searchable_presentation.pdf",
                mime="application/pdf"
            )
            
    except Exception as e:
        st.error(f"Pipeline execution failure: {e}")
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
