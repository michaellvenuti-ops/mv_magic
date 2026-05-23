import os
import shutil
import tempfile
import streamlit as st
import cv2
from PIL import Image
import pytesseract
import ocrmypdf

# 1. Page Configuration & Styling
st.set_page_config(
    page_title="Autonomous Video to Searchable PDF Lab",
    page_icon="ðŸ“„",
    layout="centered"
)

st.title("ðŸ“„ Video to Searchable PDF Lab")
st.markdown("Convert presentation and lecture videos into clean, text-filtered, OCR-searchable documents.")

# Helper function for text density scanning
def calculate_text_density(image_path):
    try:
        img = Image.open(image_path)
        width, height = img.size
        total_area = width * height
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        
        text_area = 0
        n_boxes = len(data['level'])
        for i in range(n_boxes):
            if data['text'][i].strip():
                w = data['width'][i]
                h = data['height'][i]
                text_area += (w * h)
        
        return text_area / total_area
    except Exception:
        return 0.0

# 2. Sidebar / Control Panel Setup
st.sidebar.header("âš™ï¸ Configuration Settings")

threshold = st.sidebar.slider(
    "Frame Change Sensitivity", 
    min_value=0.01, max_value=0.30, value=0.05, step=0.01,
    help="Lower value = captures finer visual changes and transitions."
)

density_threshold = st.sidebar.slider(
    "Minimum Text Density Floor", 
    min_value=0.0, max_value=0.25, value=0.05, step=0.01,
    format="%.2f",
    help="Keep a frame only if text covers at least this percentage of the layout footprint."
)

# 3. Core Processing Pipeline
uploaded_file = st.file_uploader("1. Upload Your Presentation Video", type=["mp4", "avi", "mov", "mkv"])

if uploaded_file is not None:
    st.success("Video uploaded successfully to memory buffer!")
    
    # Action Trigger Button
    if st.button("ðŸš€ Generate Searchable PDF", use_container_width=True):
        
        # Setup temporary directories so we don't pollute local file spaces
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = os.path.join(tmp_dir, "extracted_frames")
            os.makedirs(out_dir, exist_ok=True)
            
            # Save uploaded video stream to a temporary physical file path for OpenCV to read
            temp_video_path = os.path.join(tmp_dir, uploaded_file.name)
            with open(temp_video_path, "wb") as f:
                f.write(uploaded_file.read())
                
            status_log = st.empty()
            progress_bar = st.progress(0)
            
            # --- PHASE 1: EXTRACTION ---
            status_log.info("ðŸŽ¬ Step 1: Opening video channel and tracking frame arrays...")
            cam = cv2.VideoCapture(temp_video_path)
            success, frame = cam.read()
            
            if not success:
                st.error("Error: Failed to process video source parameters.")
                cam.release()
                st.stop()
                
            prev_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            count = 0
            raw_paths = []
            
            first_path = os.path.join(out_dir, f"frame_{count:04d}.jpg")
            cv2.imwrite(first_path, frame)
            raw_paths.append(first_path)
            
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
                    raw_paths.append(img_path)
                    prev_gray = gray
                    
            cam.release()
            progress_bar.progress(30)
            status_log.info(f"ðŸ“¸ Phase Complete: Extracted {len(raw_paths)} unique visual frame baselines.")
            
            # --- PHASE 2: DENSITY FILTERING ---
            status_log.info(f"ðŸ” Step 2: Evaluating content footprint (Filtering frames under {density_threshold*100:.1f}% text)...")
            filtered_paths = []
            
            for idx, path in enumerate(raw_paths):
                density = calculate_text_density(path)
                if density >= density_threshold:
                    filtered_paths.append(path)
                
                # Dynamic progress scaling from 30% to 60%
                filter_progress = 30 + int((idx / len(raw_paths)) * 30)
                progress_bar.progress(filter_progress)
                
            status_log.info(f"ðŸŽ¯ Filter Complete: Kept {len(filtered_paths)} frames exceeding text requirements.")
            
            if not filtered_paths:
                st.error("Error: All pages fell below your specified text density slider value.")
                st.stop()
                
            # --- PHASE 3: STITCH & OCR ---
            progress_bar.progress(65)
            status_log.info("ðŸ“‚ Step 3: Binding remaining document arrays into temporary file storage...")
            
            temp_pdf = os.path.join(tmp_dir, 'temp_raw_images.pdf')
            final_output_pdf = os.path.join(tmp_dir, 'final_searchable_presentation.pdf')
            
            images = [Image.open(f).convert('RGB') for f in filtered_paths]
            images[0].save(temp_pdf, save_all=True, append_images=images[1:])
            
            progress_bar.progress(80)
            status_log.info("ðŸ¤– Step 4: Compiling document layers into searchable text vectors via OCRmyPDF...")
            
            try:
                ocrmypdf.ocr(temp_pdf, final_output_pdf, deskew=False)
                progress_bar.progress(100)
                status_log.success("ðŸŽ‰ Pipeline Successful! Your file is compiled and ready.")
                
                # Read the finished physical file binary to pass it to the web browser download button
                with open(final_output_pdf, "rb") as pdf_file:
                    st.download_button(
                        label="ðŸ“¥ Download Searchable PDF",
                        data=pdf_file.read(),
                        file_name="processed_presentation.pdf",
                        mime="application/pdf",
                        use_container_width=True
                    )
            except Exception as e:
                st.error(f"OCR Exception Engine Trap: {str(e)}")
