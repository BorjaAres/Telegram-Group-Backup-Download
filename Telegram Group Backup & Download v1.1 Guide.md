# Telegram Group Backup & Download v1.1

A simple app for backing up Telegram groups, organizing them into forum topics, downloading selected topics, and cleaning duplicate files.

## What it does

- Copies Telegram groups into destination groups with topics.
- Keeps progress, so backups continue from where they stopped.
- Avoids copying duplicate files.
- Downloads whole groups or selected topics into folders.
- Scans destination topics for files that were added outside the app.
- Repairs copied messages that are missing hidden links.
- Finds duplicate files already inside a destination topic and lets you delete selected duplicates.
- Keeps preview images close to the file they belong to, instead of copying loose image spam.

## Basic Setup

1. Open the app.
2. Go to Login / Setup.
3. Enter your Telegram API details and phone number.
4. Log in.
5. Load your groups.

You only need to do this again if Telegram asks you to log in, or if you move the app to a fresh computer.

## Copy Projects

Use Project Builder to create a copy project.

1. Choose or create the destination group.
2. Add source groups.
3. Match each source topic to a destination topic.
4. Use Create when the destination topic does not exist.
5. Use Existing when you want to copy into a topic that is already there.
6. Save the project.

Many source topics can point to the same destination topic.

## Running Projects

Go to Run.

- Select a project and click Start Project.
- While a project is running, select another project and click Add to Queue.
- Waiting projects run automatically after the current one finishes.
- The current project stays at the top of the queue.
- You can move waiting projects up or down, remove them, skip the current project, or move the current project to the end.

You do not need a separate Start Queue button.

## Deep Duplicate Check

Normally leave this off.

Turn on Deep duplicate check only when files were added to the destination topic by other means, not copied by this app.

It is slower because the app must inspect the destination topic before copying.

## Downloads

Use Downloads to create download projects.

- Choose the source group.
- Choose the topics to download.
- Choose the output folder.
- By default, downloads focus on files.
- Optional media types can be enabled if needed.

Downloads keep progress, so stopping and starting again continues from the saved point.

## Clean Existing Topics

Use Clean to find duplicates already inside a destination topic.

1. Choose a destination group.
2. Load topics.
3. Select the topics to scan.
4. Click Scan Duplicates.
5. Review the results.
6. Delete only the selected duplicates.

Cleaning does not delete automatically. You choose what to delete.

## Image Handling

v1.1 is stricter with images.

Images are copied only when they appear to belong to a copied file:

- the file replies to the image, or
- the image is within 2 minutes of the copied file.

The app copies at most 3 preview images total for each copied file.

If the file is skipped as a duplicate, its nearby images are skipped too.

GIFs are skipped.

## Tips

- Start with small projects if you are testing a new setup.
- Keep Deep duplicate check off unless you specifically need it.
- Watch the Run log for progress, skipped files, and errors.
- If Telegram blocks forwarding from a protected group, the app records the error and moves on.
- If you stop a project, run it again later and it will continue from saved progress.
