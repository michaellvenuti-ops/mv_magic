import os
import shutil
import tempfile
import streamlit as st
import cv2
from PIL import Image
import ocrmypdf

st.set_page_config(page_title="Visual Video to PDF Lab", layout="wide")
st.title("📸 Visual Video to Searchable PDF Lab (Manual Review)")

# --- FIXED: PERSISTENT STATE MANAGEMENT ---
if "raw_paths" not in st.session_state:
    st.session_state.raw_paths = []
if "working_dir" not in st.session_state:
    # Create a permanent workspace directory for this user session
    st.session_state.working_dir = tempfile.mkdtemp()

# Sidebar Control Center
with st.sidebar:
    st.header("Control Panel")
    uploaded_file = st.file_uploader("Source Video", type=["mp4", "avi", "mov", "mkv"],
                                     help="Path to the presentation video file you want to process.")
    
    sens_threshold = st.slider(
        "Sensitivity Threshold", min_value=0.01, max_value=0.30, value=0.05, step=0.01,
        help="Controls visual pixel difference detection.\nLower values capture tiny visual shifts."
    )
    
    extract_clicked = st.button("Step 1: Extract Unique Scenes", type="primary", disabled=not uploaded_file)

# Step 1: Logic execution block
if extract_clicked:
    # Clear out any stale files from previous video runs in this session
    out_dir = os.path.join(st.session_state.working_dir, "extracted_frames")
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    
    with st.spinner("Processing frame stabilization pipeline..."):
        # Stream file payload down to the persistent session directory
        video_path = os.path.join(st.session_state.working_dir, "video.mp4")
        with open(video_path, "wb") as f:
            f.write(uploaded_file.read())
            
        cam = cv2.VideoCapture(video_path)
        success, frame = cam.read()
        if not success:
            st.error("Could not open video.")
            cam.release()
            st.stop()
            
        prev_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        count = 0
        st.session_state.raw_paths = []
        
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
                    st.session_state.raw_paths.append(p)
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
                        st.session_state.raw_paths.append(p)
                        in_transition = False
                        best_frame_candidate = None
                        lowest_diff_in_plateau = 1.0
                        settle_counter = 0
            prev_gray = gray
        cam.release()

# Step 2 & 3: Interactive Container (Now safely persists during checkbox toggles)
if st.session_state.raw_paths:
    st.subheader("📋 Step 2: Review and Select Pages to Keep")
    st.info(f"Isolated {len(st.session_state.raw_paths)} unique slide states. Check the frames you wish to include:")
    
    cols = st.columns(4)
    keep_list = []
    
    for idx, path in enumerate(st.session_state.raw_paths):
        col_target = cols[idx % 4]
        with col_target:
            # Re-verify path exists before rendering to avoid internal crashes
            if os.path.exists(path):
                img = Image.open(path)
                st.image(img, use_column_width=True)
                keep_page = st.checkbox(f"Keep Page {idx + 1}", value=True, key=f"chk_{path}")
                if keep_page:
                    keep_list.append(path)
                
    st.write("---")
    st.subheader("🚀 Step 3: Compile Selected Pages & OCR")
    
    if st.button("Compile Target Array", type="secondary", disabled=len(keep_list) == 0):
        try:
            with st.spinner("Stitching checked page arrays together and running Tesseract OCR..."):
                temp_pdf = os.path.join(st.session_state.working_dir, "temp_canvas.pdf")
                final_pdf = os.path.join(st.session_state.working_dir, "final_searchable.pdf")
                
                images = [Image.open(p).convert("RGB") for p in keep_list]
                images[0].save(temp_pdf, save_all=True, append_images=images[1:])
                
                ocrmypdf.ocr(temp_pdf, final_pdf, deskew=False)
                
            st.success("Searchable document compiled cleanly!")
            with open(final_pdf, "rb") as pdf_file:
                st.download_button(
                    label="📥 Download Final Searchable PDF",
                    data=pdf_file.read(),
                    file_name="manual_compiled_presentation.pdf",
                    mime="application/pdf"
                )
        except Exception as e:
            st.error(f"OCR Pipeline failure: {e}")
