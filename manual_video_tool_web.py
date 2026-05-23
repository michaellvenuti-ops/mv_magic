import os
import shutil
import tempfile
import streamlit as st
import cv2
from PIL import Image
import ocrmypdf

# 1. Page Configuration & Styling
st.set_page_config(
    page_title="Visual Video to Searchable PDF Lab",
    layout="wide"  # Switched to wide layout so the image grid looks fantastic
)

st.title("Video to Searchable PDF Lab")
st.markdown("---")

# Initialize session states so the web app remembers your images between page refreshes
if "raw_paths" not in st.session_state:
    st.session_state.raw_paths = []
if "temp_dir_path" not in st.session_state:
    st.session_state.temp_dir_path = None

# Sidebar Controls
st.sidebar.header("Configuration Settings")
threshold = st.sidebar.slider(
    "Frame Change Sensitivity", 
    min_value=0.01, max_value=0.30, value=0.05, step=0.01,
    help="Lower value = captures finer visual changes and transitions."
)

# --- STEP 1: UPLOAD AND EXTRACT ---
st.subheader("Step 1: Upload and Parse Video")
uploaded_file = st.file_uploader("Upload your presentation video file", type=["mp4", "avi", "mov", "mkv"])

if uploaded_file is not None:
    if st.button("Extract Unique Scenes", use_container_width=True):
        # Create a persistent temporary directory for this session's images
        if st.session_state.temp_dir_path and os.path.exists(st.session_state.temp_dir_path):
            shutil.rmtree(st.session_state.temp_dir_path)
            
        st.session_state.temp_dir_path = tempfile.mkdtemp()
        out_dir = os.path.join(st.session_state.temp_dir_path, "extracted_frames")
        os.makedirs(out_dir, exist_ok=True)
        
        # Write video to file system for OpenCV
        temp_video_path = os.path.join(st.session_state.temp_dir_path, uploaded_file.name)
        with open(temp_video_path, "wb") as f:
            f.write(uploaded_file.read())
            
        with st.spinner("Scanning video frames for distinct slide configurations..."):
            cam = cv2.VideoCapture(temp_video_path)
            success, frame = cam.read()
            
            if not success:
                st.error("Error processing video source.")
                cam.release()
                st.stop()
                
            prev_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            count = 0
            extracted_paths = []
            
            first_path = os.path.join(out_dir, f"frame_{count:04d}.jpg")
            cv2.imwrite(first_path, frame)
            extracted_paths.append(first_path)
            
            while True:
                success, frame = cam.read()
                if not success: 
                    break
                
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                frame_diff = cv2.absdiff(gray, prev_gray)
                score = frame_diff.mean() / 255.0
                
                if score > threshold:
                    count += 1
                    img_path = os.path.join(out_dir, f"frame_{count:04d}.jpg")
                    cv2.imwrite(img_path, frame)
                    extracted_paths.append(img_path)
                    prev_gray = gray
                    
            cam.release()
            st.session_state.raw_paths = extracted_paths
            st.success(f"Successfully isolated {len(extracted_paths)} unique frames!")

# --- STEP 2: INTERACTIVE VISUAL FILTERING & COMPILING ---
if st.session_state.raw_paths:
    st.markdown("---")
    st.subheader("Step 2: Review and Select Pages to Keep")
    st.write("Uncheck any thumbnails below that represent blank screens, duplicate layout loops, or unwanted pages.")
    
    # Render a responsive, clean multi-column grid matrix for the images
    cols = st.columns(4)  # 4 thumbnail cards per horizontal row layout
    keep_list = []
    
    for idx, path in enumerate(st.session_state.raw_paths):
        col_index = idx % 4
        with cols[col_index]:
            # Open the image file from server cache and render it as a mini-card
            img = Image.open(path)
            st.image(img, use_container_width=True)
            
            # Use a unique structural key identifier for each check state matrix component
            filename = os.path.basename(path)
            is_checked = st.checkbox(f"Keep Page {idx+1}", value=True, key=f"check_{filename}")
            if is_checked:
                keep_list.append(path)
                
    st.markdown("---")
    st.subheader("Step 3: Compile Your Document")
    st.write(f"Currently selected layout footprint: **{len(keep_list)} out of {len(st.session_state.raw_paths)}** frames will be included.")
    
    if st.button("🚀 Compile Selected Pages & Run OCR", use_container_width=True):
        if not keep_list:
            st.error("Please keep at least 1 page to compile a PDF.")
        else:
            status_log = st.empty()
            progress_bar = st.progress(0)
            
            temp_pdf = os.path.join(st.session_state.temp_dir_path, 'temp_raw_images.pdf')
            final_output_pdf = os.path.join(st.session_state.temp_dir_path, 'final_searchable_presentation.pdf')
            
            status_log.info("Binding selected frame layout matrices...")
            images = [Image.open(f).convert('RGB') for f in keep_list]
            images[0].save(temp_pdf, save_all=True, append_images=images[1:])
            
            progress_bar.progress(50)
            status_log.info("Injecting searchable character text mapping arrays via OCRmyPDF...")
            
            try:
                ocrmypdf.ocr(temp_pdf, final_output_pdf, deskew=False)
                progress_bar.progress(100)
                status_log.success("Pipeline Compiled Successfully!")
                
                with open(final_output_pdf, "rb") as pdf_file:
                    st.download_button(
                        label="📥 Download Your Custom Searchable PDF",
                        data=pdf_file.read(),
                        file_name="custom_presentation.pdf",
                        mime="application/pdf",
                        use_container_width=True
                    )
            except Exception as e:
                st.error(f"OCR Error encountered: {str(e)}")
