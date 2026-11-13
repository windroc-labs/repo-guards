pub struct Id {
    pub raw: u32,
}

impl Id {
    pub fn len(&self) -> u64 {
        self.raw as u64
    }
}

impl From<u32> for Id {
    fn from(raw: u32) -> Self {
        Id { raw }
    }
}

impl From<String> for Id {
    fn from(text: String) -> Self {
        Id { raw: text.len() as u32 }
    }
}

pub trait Len {
    fn len(&self) -> usize;
}

impl Len for Id {
    fn len(&self) -> usize {
        1
    }
}
